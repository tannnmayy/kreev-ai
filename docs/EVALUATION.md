# What has and hasn't been proven

This page is kept honest on purpose: a number appears here only if it was measured, and it says how. Anything else is listed as
not yet tested.

## Where things stand

Last updated 5 November 2026.

| Claim | Status | How it was checked |
| --- | --- | --- |
| The one command works on a clean machine | **Verified on the current code** | A throwaway WSL Ubuntu 24.04 with only Docker: `docker compose up --build` printed "reproduced: YES" in about 5 minutes, with 0 agent errors. On an earlier build, a Windows browser also used the containers through the published ports |
| Unit tests | **609 pass** | `pytest tests` |
| Real-life deck | **58 of 58 runnable cards pass; 19 not run** | `ala.bench.deck`. The other 19 need a person, a call or a model, and don't count as passes |
| A real call through LiveKit, typed | **Verified** | `ala.bench.smoke` joins a room with a real server, agent and web page, with 0 agent errors |
| The page in a real browser, typed | **Verified** | Telugu order, budget swap, product cards and read-back sheet |
| Vercel token function | Partly | Run locally with fake requests: refuses a wrong invite, and the token carries the agent dispatch. Not deployed |
| Real language models and prompts | **Not tested** | Needs keys. The JSON path is tested only against scripted responses |
| Real listening and voice | **Not tested** | Needs keys |
| The voice path over real WebRTC audio | **13 of 13 checks, in two runs in a row** | `ala.bench.voicecheck` streams computer-generated English speech into a real room as a microphone would. VAD, LiveKit's barge-in, perception, Smart Turn, the coordinator and playback are all real. Recognition and speech are stand-ins, so this tests timing and state, not hearing |
| Barge-in and perception on human speech | **Not tested** | The audio so far is computer-generated English. No human voice, no Telugu, no echo from a real speaker |
| Telugu quality | **Not reviewed** | Needs a native speaker |
| Latency | **Not measured with real services** | With instant stand-ins, Ala's audio starts about 0.8 s after the speech ends, which is the floor. Real recognition and voice add to it |
| Deployment on LiveKit Cloud and Vercel | **Not done** | Whether the 2.2 GB image is accepted is unknown until it's tried |
| Public benchmarks | **Not run** | See below |
| KREEV-Bench | **Tool ready, not run** | All 42 scenarios run end to end against scripted customers with no model. Pass rates need real models |

## Layers of testing

### 1. Unit tests (609)

Numbers and quantities in both languages, cue words, cart and order logic, the coordinator, the quota router, perception and the
brain, with models replaced by scripted responses. They also cover what must never happen, such as a model reaching
`place_order`.

### 2. The voice check (13 checks)

`ala.bench.voicecheck` is a customer who really speaks, into a real LiveKit room. Run the agent with `ALA_STANDIN=1`, which replaces
only recognition and speech. The check covers barge-in, a stop word, a backchannel, a quiet voice in the room, a yes that cuts off
the read-back, and a self-correction. It needs Windows' speech voices or a folder of WAV files, so it isn't part of the Docker
check.

Its first run found six bugs that 588 passing tests had missed:

| Bug | Effect | Fix |
| --- | --- | --- |
| The ledger remembered a refused order | After cutting off a read-back once, the customer could **never** place the order by voice. Every yes replayed the read-back | Only actions that happened are remembered |
| Ala waited for a transcript that had already arrived | About a second added to every turn whenever the words beat the VAD. Reply time went from 1.34 s to 0.81 s | A transcript just before the end of speech counts |
| Barge-in paused Ala before the words arrived | "Mm hmm" became a turn of its own, because Ala was no longer speaking when it was judged | An utterance keeps the overlap it began with |
| Words before a stop word came back | "What's in my cart. Stop. Place the order." read the cart and lost the order | A stop word closes what came before it |
| Asking for the cart and the order together | The order request was dropped | The read-back answers both |
| A quiet voice was judged on the previous segment, and "the news at nine" counted as shop talk | A television across the room got a reply | Loudness is measured from the audio when the words arrive, and a bare number inside a sentence isn't shopping |

The tests had missed these because text runs skipped the coordinator and never reported how much of a reply was heard. The deck and
KREEV-Bench now go through both.

### 3. The real-life deck (77 cards)

`scenarios/deck.yaml` holds weird situations from real life. Each is checked against the final cart and orders, not by reading
the reply.

| Family | Cards | Run automatically |
| --- | --- | --- |
| The room (TV, cough, side talk, whispering) | 9 | 4 |
| Speech (accents, mixing, slips) | 11 | 10 |
| Turn-taking | 8 | 4 |
| Memory and vagueness | 11 | 11 |
| Shop logic | 11 | 11 |
| Order integrity | 11 | 11 |
| Adversarial and off-topic | 5 | 4 |
| System and network | 6 | 1 |
| Culture and season (Karthika, Deepavali) | 5 | 5 |

The automatic cards use text, or the overlap classifier on transcribed words. The rest are a script for a person to perform on a
real call, and `web/cards.json` shows them to testers in the page.

### 4. KREEV-Bench (42 simulated customers)

`ala.bench.simulate` has a model play a customer with a hidden goal, in the style of τ-bench. Goals include a budget that changes
partway through, a switch to Telugu at the third message, a false claim about an earlier price, and a card number offered over
voice. The score is the final database state, checked in code, so no model grades the result.

- **pass^k:** a scenario counts only if all k runs pass. That matters more than an average, because a live evaluation is a single
  run and the judge doesn't care that it usually works.
- **Language match** and **style violations per reply** are also recorded.
- Free quota limits how much can run in a day, so `--sample` spreads it over days. Every run is saved in `reports/`, and every
  transcript can be read and rescored.
- A simulated customer isn't a person. Failures have to be read before they're believed, since a weak simulator can fail by
  itself.

### 5. The bake-off

`ala.bench.bakeoff`, on recordings from [clips/README.md](../clips/README.md):

- **Listening:** word and character error, and above all whether the words that matter survived. Mishearing *రొయ్యలు* costs far more
  than dropping *అండి*.
- **Thinking:** time to answer for each candidate, on the real prompts.
- **Turn-taking:** an ablation of silence only, voice, words, and voice plus words, reporting false cut-offs and extra waits. A
  layer stays only if it earns its place.
- **Voice:** a blind listening test by a Telugu speaker.

### 6. Public benchmarks

Planned, none run yet.

| Priority | Benchmark | What it would show |
| --- | --- | --- |
| Must | Full-Duplex-Bench v3 | Self-corrections and tool use under real disfluency, comparable with DUET's 0.88 |
| Must | Full-Duplex-Bench v1 and v1.5 | Turn-taking, pauses and overlap |
| Must | Telugu sets from IndicVoices and Vistaar | Listening accuracy on many speakers, beyond our own recordings |
| Should | Smart Turn's test set | End-of-turn accuracy by language |
| Should | VoiceAgentBench, Telugu subset | Spoken tool use in Telugu |
| Should | Audio MultiChallenge | Multi-turn memory and self-coherence |
| Could | MSI-Bench and EVA-Bench | Several speakers, and a full end-to-end score |

Metrics borrowed from IHBench (recovery after an interruption), EchoChain (state updates under interruption) and TRACE (noise) will
be added to the KREEV-Bench report. Public benchmarks show where Ala stands. The deck and KREEV-Bench show whether it works here.

## The daily routine

Every day until submission:

1. `pytest` and the deck after every change, and the voice check after any change to how Ala listens or speaks.
2. A sample of KREEV-Bench, within the free quota.
3. One real call on the deployed link, running a few human cards from the deck, on a phone.
4. Anything that went wrong becomes a new card, so it can't come back unnoticed.
