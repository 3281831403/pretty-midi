"""Acceptance tests for read/write/read identity preservation.

These tests exercise the archival round-trip requirements:

1. Key signatures keep their sharp/flat spelling (Gb stays Gb, not F#).
2. Tracks containing only control changes / pitch bends (no notes) survive
   as full instruments, including control events that appear before the
   first note.
3. Channels are taken from the source file, not reassigned.
4. Track names are recovered no matter where in the track they appear.
5. Notes are assigned to the program active at their note-on time.
6. Hand-constructed ``KeySignature(6, 0.)`` and ``Instrument(0)`` keep the
   library's historical write defaults.

Run with:  python -m pytest accept_pm_id.py -v
"""
import io

import mido
import pytest

import pretty_midi


def _roundtrip(mido_file):
    """Read a mido MidiFile with pretty_midi, write it back and read again.

    Returns the two PrettyMIDI objects and the re-written mido MidiFile.
    """
    encoded = io.BytesIO()
    mido_file.save(file=encoded)
    encoded.seek(0)
    pm_loaded = pretty_midi.PrettyMIDI(encoded)
    rewritten = io.BytesIO()
    pm_loaded.write(rewritten)
    rewritten.seek(0)
    pm_reread = pretty_midi.PrettyMIDI(rewritten)
    rewritten.seek(0)
    return pm_loaded, pm_reread, mido.MidiFile(file=rewritten)


def _make_timing_track():
    return mido.MidiTrack([
        mido.MetaMessage('time_signature', time=0, numerator=4,
                         denominator=4),
        mido.MetaMessage('set_tempo', time=0, tempo=mido.bpm2tempo(120))])


# All 15 major and 15 minor spellings mido can store.
KEY_SPELLINGS = [
    'Cb', 'Gb', 'Db', 'Ab', 'Eb', 'Bb', 'F', 'C', 'G', 'D', 'A', 'E', 'B',
    'F#', 'C#',
    'Abm', 'Ebm', 'Bbm', 'Fm', 'Cm', 'Gm', 'Dm', 'Am', 'Em', 'Bm', 'F#m',
    'C#m', 'G#m', 'D#m', 'A#m']


@pytest.mark.parametrize('key_name', KEY_SPELLINGS)
def test_key_signature_spelling_round_trips(key_name):
    midi_file = mido.MidiFile(ticks_per_beat=480)
    track = _make_timing_track()
    track.append(mido.MetaMessage('key_signature', time=0, key=key_name))
    track.append(mido.MetaMessage('end_of_track', time=0))
    midi_file.tracks.append(track)

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    written_keys = [event.key for event in rewritten.tracks[0]
                    if event.type == 'key_signature']
    assert written_keys == [key_name]
    assert pm_loaded.key_signature_changes[0].key_name == key_name
    assert pm_reread.key_signature_changes[0].key_name == key_name
    # The numeric identity is unchanged as well.
    assert (pm_loaded.key_signature_changes[0].key_number ==
            pm_reread.key_signature_changes[0].key_number)


def test_enharmonic_spellings_are_not_rewritten():
    # The exact cases called out in the bug report.
    cases = {'Gb': 'Gb', 'Cb': 'Cb', 'C#': 'C#', 'Ebm': 'Ebm'}
    midi_file = mido.MidiFile(ticks_per_beat=480)
    track = _make_timing_track()
    for index, key_name in enumerate(cases):
        track.append(mido.MetaMessage(
            'key_signature', time=index * 480, key=key_name))
    track.append(mido.MetaMessage('end_of_track', time=0))
    midi_file.tracks.append(track)

    _, _, rewritten = _roundtrip(midi_file)
    written_keys = [event.key for event in rewritten.tracks[0]
                    if event.type == 'key_signature']
    assert written_keys == list(cases.values())


def test_control_only_track_survives_as_instrument():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('control_change', time=0, channel=2, control=64,
                     value=127),
        mido.Message('pitchwheel', time=120, channel=2, pitch=8191),
        mido.Message('control_change', time=120, channel=2, control=64,
                     value=0),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    assert len(pm_loaded.instruments) == 1
    instrument = pm_loaded.instruments[0]
    assert instrument.notes == []
    assert len(instrument.control_changes) == 2
    assert len(instrument.pitch_bends) == 1
    assert instrument.channel == 2

    assert len(pm_reread.instruments) == 1
    reread = pm_reread.instruments[0]
    assert len(reread.control_changes) == 2
    assert len(reread.pitch_bends) == 1
    assert reread.channel == 2

    written_channels = {
        event.channel for event in rewritten.tracks[1]
        if hasattr(event, 'channel')}
    assert written_channels == {2}


def test_drum_control_only_track_survives_as_drum_instrument():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('control_change', time=0, channel=9, control=64,
                     value=127),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    instrument = pm_loaded.instruments[0]
    assert instrument.is_drum is True
    assert instrument.channel == 9
    assert len(instrument.control_changes) == 1
    assert pm_reread.instruments[0].is_drum is True
    written_channels = {
        event.channel for event in rewritten.tracks[1]
        if hasattr(event, 'channel')}
    assert written_channels == {9}


def test_control_events_before_first_note_stay_on_same_instrument():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('control_change', time=0, channel=4, control=7,
                     value=100),
        mido.Message('pitchwheel', time=0, channel=4, pitch=10),
        mido.Message('program_change', time=0, channel=4, program=20),
        mido.Message('note_on', time=240, channel=4, note=60, velocity=90),
        mido.Message('note_on', time=240, channel=4, note=60, velocity=0),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, _ = _roundtrip(midi_file)

    assert len(pm_loaded.instruments) == 1
    instrument = pm_loaded.instruments[0]
    assert len(instrument.notes) == 1
    assert len(instrument.control_changes) == 1
    assert len(instrument.pitch_bends) == 1
    assert instrument.program == 20
    assert instrument.channel == 4

    assert len(pm_reread.instruments) == 1
    reread = pm_reread.instruments[0]
    assert len(reread.notes) == 1
    assert len(reread.control_changes) == 1
    assert len(reread.pitch_bends) == 1


def test_channels_are_preserved_from_source_file():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    for channel in (0, 2, 5, 14):
        midi_file.tracks.append(mido.MidiTrack([
            mido.Message('program_change', time=0, channel=channel,
                         program=channel),
            mido.Message('note_on', time=0, channel=channel, note=60,
                         velocity=90),
            mido.Message('note_on', time=240, channel=channel, note=60,
                         velocity=0),
            mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    assert sorted(i.channel for i in pm_loaded.instruments) == \
        [0, 2, 5, 14]
    assert sorted(i.channel for i in pm_reread.instruments) == \
        [0, 2, 5, 14]
    written = [
        sorted({event.channel for event in track
                if hasattr(event, 'channel')})
        for track in rewritten.tracks[1:]]
    assert written == [[0], [2], [5], [14]]


def test_track_name_after_first_note_is_recovered():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('program_change', time=0, channel=5, program=0),
        mido.Message('note_on', time=0, channel=5, note=60, velocity=100),
        mido.MetaMessage('track_name', time=0, name='Late Name'),
        mido.Message('note_on', time=480, channel=5, note=60, velocity=0),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    assert pm_loaded.instruments[0].name == 'Late Name'
    assert pm_reread.instruments[0].name == 'Late Name'
    written_names = [event.name for event in rewritten.tracks[1]
                     if event.type == 'track_name']
    assert written_names == ['Late Name']


def test_note_uses_program_active_at_note_on():
    midi_file = mido.MidiFile(ticks_per_beat=480)
    midi_file.tracks.append(_make_timing_track())
    # Note 64 is turned on under program 40 and held across a program change
    # to 41; note 65 is turned on after the change and belongs to program 41.
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('program_change', time=0, channel=3, program=40),
        mido.Message('note_on', time=0, channel=3, note=64, velocity=90),
        mido.Message('program_change', time=240, channel=3, program=41),
        mido.Message('note_on', time=0, channel=3, note=65, velocity=90),
        mido.Message('note_on', time=240, channel=3, note=64, velocity=0),
        mido.Message('note_on', time=240, channel=3, note=65, velocity=0),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, _ = _roundtrip(midi_file)

    by_program = {instrument.program:
                  sorted(note.pitch for note in instrument.notes)
                  for instrument in pm_loaded.instruments}
    assert by_program == {40: [64], 41: [65]}
    by_program_reread = {instrument.program:
                         sorted(note.pitch for note in instrument.notes)
                         for instrument in pm_reread.instruments}
    assert by_program_reread == {40: [64], 41: [65]}


def test_full_identity_after_read_write_read():
    """All the requirements at once on a single multi-track file."""
    midi_file = mido.MidiFile(ticks_per_beat=480)
    timing = _make_timing_track()
    for index, key_name in enumerate(['Gb', 'Cb', 'C#', 'Ebm']):
        timing.append(mido.MetaMessage(
            'key_signature', time=index * 480, key=key_name))
    timing.append(mido.MetaMessage('end_of_track', time=0))
    midi_file.tracks.append(timing)

    # CC/bend-only track on channel 2.
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('control_change', time=0, channel=2, control=64,
                     value=127),
        mido.Message('pitchwheel', time=120, channel=2, pitch=8191),
        mido.MetaMessage('end_of_track', time=0)]))
    # Named note track on channel 5, name after the first note.
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('program_change', time=0, channel=5, program=0),
        mido.Message('note_on', time=0, channel=5, note=60, velocity=100),
        mido.MetaMessage('track_name', time=0, name='Late Name'),
        mido.Message('note_on', time=480, channel=5, note=60, velocity=0),
        mido.MetaMessage('end_of_track', time=0)]))
    # Program change while a note is held on channel 3.
    midi_file.tracks.append(mido.MidiTrack([
        mido.Message('program_change', time=0, channel=3, program=40),
        mido.Message('note_on', time=0, channel=3, note=64, velocity=90),
        mido.Message('program_change', time=240, channel=3, program=41),
        mido.Message('note_on', time=240, channel=3, note=64, velocity=0),
        mido.MetaMessage('end_of_track', time=0)]))

    pm_loaded, pm_reread, rewritten = _roundtrip(midi_file)

    # Key spellings.
    assert [event.key for event in rewritten.tracks[0]
            if event.type == 'key_signature'] == \
        ['Gb', 'Cb', 'C#', 'Ebm']

    # Three instruments: the control-only one plus the two note tracks.
    assert len(pm_loaded.instruments) == 3
    assert len(pm_reread.instruments) == 3

    summary = sorted(
        (instrument.channel, instrument.program, instrument.name,
         len(instrument.notes), len(instrument.control_changes),
         len(instrument.pitch_bends))
        for instrument in pm_reread.instruments)
    assert summary == [
        (2, 0, '', 0, 1, 1),
        (3, 40, '', 1, 0, 0),
        (5, 0, 'Late Name', 1, 0, 0)]

    written_channels = sorted(
        tuple(sorted({event.channel for event in track
                      if hasattr(event, 'channel')}))
        for track in rewritten.tracks[1:])
    # Track order follows instrument creation order; only the channel set has
    # to match the source.
    assert written_channels == [(2,), (3,), (5,)]


def test_hand_built_key_signature_keeps_default_spelling():
    # KeySignature(6, 0.) must continue to write F# (the historical default).
    key_signature = pretty_midi.KeySignature(6, 0.)
    assert key_signature.key_name is None

    pm = pretty_midi.PrettyMIDI()
    pm.key_signature_changes = [key_signature]
    output = io.BytesIO()
    pm.write(output)
    output.seek(0)
    rewritten = mido.MidiFile(file=output)
    assert [event.key for event in rewritten.tracks[0]
            if event.type == 'key_signature'] == ['F#']

    # And key number 1 still writes Db.
    pm.key_signature_changes = [pretty_midi.KeySignature(1, 0.)]
    output = io.BytesIO()
    pm.write(output)
    output.seek(0)
    rewritten = mido.MidiFile(file=output)
    assert [event.key for event in rewritten.tracks[0]
            if event.type == 'key_signature'] == ['Db']


def test_hand_built_instrument_keeps_default_channel_assignment():
    pm = pretty_midi.PrettyMIDI()
    first = pretty_midi.Instrument(0)
    second = pretty_midi.Instrument(1)
    # No channel metadata on hand-built instruments.
    assert first.channel is None
    assert second.channel is None
    pm.instruments = [first, second]

    output = io.BytesIO()
    pm.write(output)
    output.seek(0)
    rewritten = mido.MidiFile(file=output)
    channels = [
        sorted({event.channel for event in track
                if hasattr(event, 'channel')})
        for track in rewritten.tracks[1:]]
    # Historical behavior: channels assigned positionally, skipping 9.
    assert channels == [[0], [1]]
