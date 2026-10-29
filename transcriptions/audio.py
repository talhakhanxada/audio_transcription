import subprocess
import wave
from django.conf import settings


class InvalidAudio(Exception):
    pass


def normalize(source, target):
    """Decode the actual file content; the extension alone is not trusted."""
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
             "-map", "0:a:0", "-t", str(settings.MAX_AUDIO_SECONDS + 1),
             "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(target)],
            check=True, capture_output=True, timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        raise InvalidAudio("The file could not be decoded as audio.") from exc


def duration(path):
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def chunks(path, target, seconds, skip=0):
    """Read one chunk into memory at a time, reusing a temporary WAV file."""
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        frames_per_chunk = rate * seconds
        source.setpos(min(skip * frames_per_chunk, source.getnframes()))
        index = skip
        while frames := source.readframes(frames_per_chunk):
            with wave.open(str(target), "wb") as output:
                output.setparams(source.getparams())
                output.writeframes(frames)
            yield index, index * seconds, target
            index += 1
