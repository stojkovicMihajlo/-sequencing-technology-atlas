import pytest

from genomics_discovery.illumina.extraction import verify_stage_inputs, write


@pytest.mark.parametrize('extra', [{}, {'image_inputs': []}])
def test_paper_only_matches_the_real_worker_empty_archive(tmp_path, extra):
    write(tmp_path/'command.json', {'argv':['codex','exec','-'], **extra})
    verify_stage_inputs(tmp_path, [], {})


@pytest.mark.parametrize('extra', [{}, {'image_inputs': []}, {'image_inputs': None}])
def test_missing_or_invalid_inventory_cannot_hide_requested_image(tmp_path, extra):
    from genomics_discovery.illumina.extraction import sha
    image=tmp_path/'input.png';image.write_bytes(b'original')
    delivery=[{'attachment':str(image), 'attachment_sha256':sha(image)}]
    write(tmp_path/'command.json', {'argv':['codex','exec','--image',str(image),'-'], **extra})
    with pytest.raises(ValueError, match='image inputs differ'):
        verify_stage_inputs(tmp_path, delivery, {})


def test_unrequested_command_line_image_is_rejected(tmp_path):
    write(tmp_path/'command.json', {'argv':['codex','exec','--image','unexpected.png','-']})
    with pytest.raises(ValueError, match='did not attach'):
        verify_stage_inputs(tmp_path, [], {})
