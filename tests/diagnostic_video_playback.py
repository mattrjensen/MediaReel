"""
Spike: can Qt's QMediaPlayer decode the videos this app deals with?

Standalone diagnostic (not a pytest test — it needs real video files and
plays audio/video). For each file given on the command line it loads the
clip into a QMediaPlayer with a QVideoSink attached (so decoded frames can be
counted without any window on screen), plays for a couple of seconds, then
seeks and plays again.

A clip "passes" only if frames actually arrive — a player can report
LoadedMedia and even PlayingState while producing nothing, which is the
failure mode that matters (a black box in the preview).

Usage:
    python tests/diagnostic_video_playback.py <video> [<video> ...]
"""
import os
import sys
import time

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtMultimedia import (
    QAudioOutput, QMediaFormat, QMediaPlayer, QVideoSink,
)

PLAY_SECONDS = 2.0
LOAD_TIMEOUT = 10.0


def pump(seconds=None, until=None, timeout=5.0):
    """Run the event loop for a fixed time, or until a condition is true."""
    app = QCoreApplication.instance()
    end = time.monotonic() + (seconds if seconds is not None else timeout)
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


def probe(path):
    result = {'file': os.path.basename(path)}
    player = QMediaPlayer()
    audio = QAudioOutput()
    player.setAudioOutput(audio)
    sink = QVideoSink()
    player.setVideoSink(sink)

    frames = []
    sink.videoFrameChanged.connect(
        lambda f: frames.append((f.width(), f.height(), f.startTime())))
    errors = []
    player.errorOccurred.connect(lambda err, msg: errors.append(msg))

    player.setSource(QUrl.fromLocalFile(path))
    loaded = pump(until=lambda: player.mediaStatus() in (
        QMediaPlayer.LoadedMedia, QMediaPlayer.InvalidMedia) or bool(errors),
        timeout=LOAD_TIMEOUT)
    result['status'] = player.mediaStatus().name
    result['has_video'] = player.hasVideo()
    result['has_audio'] = player.hasAudio()
    result['duration_ms'] = player.duration()

    if not loaded or errors or player.mediaStatus() != QMediaPlayer.LoadedMedia:
        result['error'] = errors[0] if errors else 'did not load'
        result['frames'] = 0
        return result

    player.play()
    pump(seconds=PLAY_SECONDS)
    result['frames'] = len(frames)
    result['size'] = f'{frames[0][0]}x{frames[0][1]}' if frames else '-'
    result['position_ms'] = player.position()

    # Seek to the middle and make sure frames arrive from there. play() is
    # called again because a clip shorter than PLAY_SECONDS has already hit
    # the end and stopped, and a stopped player doesn't deliver frames after
    # a seek.
    before = len(frames)
    target = max(player.duration() // 2, 0)
    player.setPosition(target)
    player.play()
    pump(seconds=1.0)
    result['seek_frames'] = len(frames) - before
    result['seek_position_ms'] = player.position()

    player.pause()
    player.stop()
    if errors:
        result['error'] = errors[0]
    return result


def main(paths):
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    fmt = QMediaFormat()
    dec = fmt.supportedVideoCodecs(QMediaFormat.Decode)
    print('Decodable video codecs:', sorted(c.name for c in dec))
    print()

    failures = 0
    for path in paths:
        r = probe(path)
        ok = r.get('frames', 0) > 0 and r.get('seek_frames', 0) > 0 and 'error' not in r
        failures += not ok
        print(('PASS ' if ok else 'FAIL ') + r['file'])
        for k, v in r.items():
            if k != 'file':
                print(f'      {k}: {v}')
    print()
    print(f'{len(paths) - failures}/{len(paths)} passed')
    return 1 if failures else 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
