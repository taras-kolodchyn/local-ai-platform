"""Install content-verified MiniLM assets once for offline local inference."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen

REVISION = 'df500e56a59fc34f37322735a40eff666200312e'
BASE = f'https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/resolve/{REVISION}/'
FILES = {
    'onnx/model_qint8_arm64.onnx': '3573b6b9593cb2f75987a31815d409ca3dd8808629118fd20451bb1a5d90cec7',
    'tokenizer.json': 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66',
}


def install():
    directory = Path(__file__).resolve().parents[1] / '.local' / 'models' / 'reranker' / REVISION
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        target = directory / Path(name).name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temp = Path(stream.name)
        try:
            with urlopen(BASE + name, timeout=120) as source, temp.open('wb') as dest:
                shutil.copyfileobj(source, dest)
            if hashlib.sha256(temp.read_bytes()).hexdigest() != expected:
                raise ValueError(f'Checksum mismatch for {name}')
            temp.chmod(0o644)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


if __name__ == '__main__':
    install()
