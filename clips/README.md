# Recording sheet

Real voices, for the two measurements no benchmark can make for us. Record on a phone in a normal room, some with a fan on or
the TV in the background, as WAV or converted to it. Keep the file names below and drop the files in this folder. The audio
isn't committed: people's voices stay on this machine.

    uv run python -m ala.bench.bakeoff listen clips/lines.tsv     # which service hears these best
    uv run python -m ala.bench.bakeoff turns  clips/turns.tsv     # does each turn-taking layer earn its place

`lines.tsv` is file, what was said, then the words that matter. If a line came out differently from the sheet, change the sheet
so it says what was really said. Two or three speakers are worth far more than one speaker repeating everything.

`turns.tsv` is file, finished or unfinished, transcript. For an unfinished clip, stop mid-thought and stay silent for two
seconds, the way a person does while reading the label on a packet. Don't trail off artificially.
