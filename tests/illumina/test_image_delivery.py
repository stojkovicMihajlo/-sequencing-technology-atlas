"""Original raster custody and the existing native PNG delivery contract."""
import builtins
from pathlib import Path
import shutil
import struct
import subprocess
from types import SimpleNamespace
import zlib

import pytest

from genomics_discovery.illumina import extraction
from tests.illumina.test_illumina_batch_controls import png


@pytest.fixture
def no_pillow(monkeypatch):
    original=builtins.__import__
    def without_pillow(name,*args,**kwargs):
        if name=='PIL' or name.startswith('PIL.'):raise ImportError('Pillow absent in test environment')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',without_pillow)


def test_png_without_pillow_cannot_skip_transparency_compositing(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.png';source.write_bytes(png())
    monkeypatch.setattr(extraction.shutil,'which',lambda name:None)
    with pytest.raises(extraction.StageFailure) as error:
        extraction.stage_images([('figure:1',source)],tmp_path)
    assert error.value.category=='image_conversion_unavailable'


def test_jpeg_without_pillow_uses_png_converter_with_original_and_tool_pins(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.jpg';source.write_bytes(b'\xff\xd8\xffsynthetic JPEG transport fixture\xff\xd9')
    original_sha=extraction.sha(source);tool=tmp_path/'magick';tool.write_bytes(b'fake converter executable')
    monkeypatch.setattr(extraction.shutil,'which',lambda name:str(tool) if name=='magick' else None)
    calls=[]
    def convert(command,**kwargs):
        calls.append(command)
        if command[-1]=='-version':return SimpleNamespace(stdout='Version: ImageMagick test\n')
        if command[-1]=='info:':return SimpleNamespace(stdout='1 1')
        Path(command[-1].removeprefix('PNG:')).write_bytes(png())
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(extraction.subprocess,'run',convert)
    delivered,receipts=extraction.stage_images([('figure:1',source)],tmp_path)
    receipt=receipts[0]
    assert delivered[0].read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert extraction.sha(source)==original_sha==receipt['original_sha256']
    assert receipt['converter']['sha256']==extraction.sha(tool)
    assert receipt['converter']['command']==calls[0] and receipt['converter']['version']=='Version: ImageMagick test'
    assert not receipt['source_detail_added']


def test_missing_raster_converter_is_a_host_failure_before_delivery(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.jpg';source.write_bytes(b'\xff\xd8\xfforiginal')
    monkeypatch.setattr(extraction.shutil,'which',lambda name:None)
    with pytest.raises(extraction.StageFailure) as error:
        extraction.stage_images([('figure:1',source)],tmp_path)
    assert error.value.category=='image_conversion_unavailable'
    assert not (tmp_path/'image_delivery.json').exists()


@pytest.mark.parametrize('failure',['bad_output','corrupt_input'])
def test_failed_raster_conversion_never_claims_a_native_png(tmp_path,monkeypatch,no_pillow,failure):
    source=tmp_path/'original.jpg';source.write_bytes(b'corrupt image transport fixture')
    tool=tmp_path/'magick';tool.write_bytes(b'fake converter executable')
    monkeypatch.setattr(extraction.shutil,'which',lambda name:str(tool) if name=='magick' else None)
    def convert(command,**kwargs):
        if failure=='corrupt_input':raise subprocess.CalledProcessError(1,command,stderr=b'not a valid image')
        if command[-1]=='-version':return SimpleNamespace(stdout='Version: ImageMagick test\n')
        if command[-1]=='info:':return SimpleNamespace(stdout='1 1')
        Path(command[-1].removeprefix('PNG:')).write_bytes(b'not PNG bytes')
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(extraction.subprocess,'run',convert)
    with pytest.raises(extraction.StageFailure) as error:
        extraction.stage_images([('figure:1',source)],tmp_path)
    assert error.value.category=='image_conversion_failed'
    assert not (tmp_path/'image_delivery.json').exists()


def transparent_png(palette):
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',3,1,8,3 if palette else 6,0,0,0))
    if palette:
        raw+=chunk(b'PLTE',bytes([0,0,0,0,0,0,255,0,0]))+chunk(b'tRNS',bytes([0,255,128]))
        pixels=bytes([0,1,2])
    else:pixels=bytes([0,0,0,0,0,0,0,255,255,0,0,128])
    return raw+chunk(b'IDAT',zlib.compress(b'\x00'+pixels))+chunk(b'IEND',b'')


@pytest.mark.parametrize('palette',[False,True],ids=['RGBA','palette-tRNS'])
def test_actual_converter_preserves_foreground_and_flattens_transparent_background(tmp_path,no_pillow,palette):
    tool=shutil.which('magick') or shutil.which('convert')
    if not tool:pytest.skip('Configured ImageMagick is unavailable')
    source=tmp_path/'original.png';original=transparent_png(palette);source.write_bytes(original)
    paths,receipts=extraction.stage_images([('diagram:alpha',source)],tmp_path)
    pixels=subprocess.run([tool,str(paths[0]),'-depth','8','RGB:-'],capture_output=True,check=True).stdout
    assert pixels==bytes([255,255,255,0,0,0,255,127,127])
    assert source.read_bytes()==original
    assert receipts[0]['original_dimensions_pixels']==[3,1]==receipts[0]['attachment_dimensions_pixels']
    assert receipts[0]['visual_quality']=='requires_inspection' and not receipts[0]['source_detail_added']
    assert receipts[0]['converter']['sha256']==extraction.sha(Path(tool))


def test_converter_cannot_silently_crop_or_resize_a_display(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.png';source.write_bytes(transparent_png(False));tool=tmp_path/'magick';tool.write_bytes(b'test converter')
    monkeypatch.setattr(extraction.shutil,'which',lambda name:str(tool) if name=='magick' else None)
    def convert(command,**kwargs):
        if command[-1]=='info:':return SimpleNamespace(stdout='3 1' if str(source) in command else '1 1')
        Path(command[-1].removeprefix('PNG:')).write_bytes(png());return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(extraction.subprocess,'run',convert)
    with pytest.raises(extraction.StageFailure,match='changed original pixel dimensions'):
        extraction.stage_images([('diagram:alpha',source)],tmp_path)
    assert not (tmp_path/'image_delivery.json').exists()
