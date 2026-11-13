"""Original raster custody and the existing native PNG delivery contract."""
import builtins
from pathlib import Path
import subprocess
from types import SimpleNamespace

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


def test_png_without_pillow_remains_an_identical_copy(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.png';source.write_bytes(png())
    monkeypatch.setattr(extraction.shutil,'which',lambda name:None)
    delivered,receipt=extraction.stage_images([('figure:1',source)],tmp_path)
    assert delivered[0].read_bytes()==source.read_bytes()
    assert receipt[0]['derivation']=='byte-preserving copy' and receipt[0]['converter'] is None


def test_jpeg_without_pillow_uses_png_converter_with_original_and_tool_pins(tmp_path,monkeypatch,no_pillow):
    source=tmp_path/'original.jpg';source.write_bytes(b'\xff\xd8\xffsynthetic JPEG transport fixture\xff\xd9')
    original_sha=extraction.sha(source);tool=tmp_path/'magick';tool.write_bytes(b'fake converter executable')
    monkeypatch.setattr(extraction.shutil,'which',lambda name:str(tool) if name=='magick' else None)
    calls=[]
    def convert(command,**kwargs):
        calls.append(command)
        if command[-1]=='-version':return SimpleNamespace(stdout='Version: ImageMagick test\n')
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
        Path(command[-1].removeprefix('PNG:')).write_bytes(b'not PNG bytes')
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(extraction.subprocess,'run',convert)
    with pytest.raises(extraction.StageFailure) as error:
        extraction.stage_images([('figure:1',source)],tmp_path)
    assert error.value.category=='image_conversion_failed'
    assert not (tmp_path/'image_delivery.json').exists()
