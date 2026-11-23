# Confidential Clinical Documentation

> Based on the open-source [NVIDIA Ambient Provider reference architecture](https://github.com/NVIDIA-AI-Blueprints/ambient-provider),
> Apache-2.0, forked at release `v1.0.0` / commit `eb4c0be5` and modified by Super Protocol — see
> [`MODIFICATIONS.md`](https://github.com/Super-Protocol/confidential-clinical-documentation/blob/confidential/1.0.0/MODIFICATIONS.md)
> in [our fork](https://github.com/Super-Protocol/confidential-clinical-documentation). NVIDIA is a
> trademark of NVIDIA Corporation. This listing is not produced, endorsed or supported by NVIDIA.

Upload a recorded consultation. Get a transcript with the clinician and the patient separated, and
a SOAP or progress note drafted from it with citations back to what was said. The recording never
leaves confidential hardware, and the deployment publishes cryptographic proof of exactly which
code and which models touched it.

**Every note is a draft for a clinician to read, correct and sign.** This makes no clinical claim,
holds no regulatory clearance and is not a medical device.

---

## The one thing to read before deploying

A clinical note generated from a transcript whose speakers were separated **wrongly** is fluent,
correctly structured, uses the right headings, and attributes the patient's symptoms to the
clinician and the clinician's plan to the patient. Nothing raises. Every health check stays green.
The note arrives in seconds. A human signs it.

Upstream attributes speakers unconditionally, and in three places guesses who was speaking when it
does not know — a keyword match on phrases like "my symptoms", a cluster index read as a role in the
UI, and a prompt that asks the model to "be generous". Its default path never ran role detection at
all, so the keyword guess was the ordinary path rather than the fallback.

This listing measures instead. Every transcript is scored on four numbers computed from the
diarization result itself, and compared against floors you choose at deploy time:

| score | what it catches |
|---|---|
| speakers found | one voice on a two-person consultation, or a monologue split into a conversation |
| fraction of words inside a speaker turn | most of the audio called non-speech, so the placed words are a minority report |
| per-sentence speaker purity | speaker changes cutting across sentences — the one a plausible-looking overall split hides |
| share of the recording called speech | silence, or a format problem |

A transcript below a floor arrives with **no speaker labels**, a stated reason in a banner above it,
and the note generation told in words that the transcript is unattributed and must not be
attributed. One voice is reported as a dictation, not as an error. The score is on the transcript,
in the UI and in the pod log.

Measured end to end inside the image on four fixtures: word-weighted speaker attribution
**97.2–98.7 %**, with the dictation fixture correctly reporting one speaker where upstream's own
configuration reports two.

---

## What you are agreeing to

Four sets of weights run in a deployment of this listing. All four permit commercial
self-hosting, and three of the four add nothing to what you agree to.

| what it does | model | licence | conditions that bite |
|---|---|---|---|
| speech recognition | [Distil-Whisper large-v3](https://huggingface.co/Systran/faster-distil-whisper-large-v3), CTranslate2 conversion | **MIT** | none beyond carrying the notice; no user-count ceiling |
| speaker segmentation | [pyannote segmentation 3.0](https://huggingface.co/pyannote/segmentation-3.0), ONNX conversion | **MIT** (CNRS) | none beyond carrying the notice |
| speaker embeddings | [NVIDIA NeMo TitaNet-Large](https://huggingface.co/nvidia/speakerverification_en_titanet_large), ONNX conversion | **CC-BY-4.0** | **attribution is required.** NVIDIA is credited on the listing page, in `/licenses/models` inside the image, and in the model manifest the image digest covers |
| note generation | [Qwen3-Coder 30B A3B Instruct FP8](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8) | **Apache-2.0** | none beyond carrying the notice; no user-count ceiling |

The software is Apache-2.0 throughout: our fork of upstream, faster-whisper and CTranslate2 (MIT),
sherpa-onnx and vLLM (Apache-2.0). Each licence file travels inside the image at `/licenses`.

**Not offered, deliberately.** `Nemotron-Labs-Audex-{2B,30B-A3B}` — a plausible ASR candidate — is
under the `nvidia-oneway-noncommercial-license` and is not in this listing at any setting. The
NemoGuard content-safety weights (`license: other`) are not offered either, and upstream's NeMo
Guardrails path is removed rather than shipped switched off: it gates on a flag that defaults to
false, upstream ships no rails configuration at all, and the engine it wires points at
NVIDIA-hosted inference. **This deployment serves no content-safety model.** Clinician review is
the control, and the listing says so rather than implying a filter exists.

---

## What leaves the deployment

Nothing.

There is no external host in this deployment's configuration — no model host, no vendor API, no
telemetry endpoint. The recognition and diarization weights are **inside the image**, fetched at
build time against a manifest that names every file by sha256, so a fresh deployment does not
reach `huggingface.co` either. Generation is a Service in the same namespace with no public
address.

In front of the application's HTTP clients sits a loopback CONNECT guard whose **allowlist is
empty**. It refuses every request — any host, any port, plain HTTP included — with a 403 it counts,
and the console shows the count. Its code is `egress_guard.py` in a ConfigMap the cloud digests and
signs, so the rule and the thing enforcing the rule are the same artefact and both are inside the
published evidence.

One honest limit: a cluster space is provisioned with the platform's own network policies, which
admit the public internet minus the private ranges, and a Kubernetes NetworkPolicy a chart renders
can only widen what is permitted. A listing cannot narrow its namespace below that. The guard is in
front of the client for exactly that reason — it binds the application, not the kernel.

---

## How fast, and why it is a CPU

Upstream's own README asks for about **400 GB across five A100s**: a Parakeet 1.1B ASR NIM at 75 GB
and a 49B generation NIM on two H100s. One H100 NVL is 94 GB, and the GPU device plugin gives that
card to exactly **one** container — so an LLM server and a GPU ASR service cannot both have it.

The decision this listing makes, and declares: **the card goes entirely to generation**, and
recognition and diarization run on the pod's CPU. Measured inside the image on six threads:

| | |
|---|---|
| recognition + speaker separation | **21–23 s of wall clock per minute of audio** |
| a 15-minute consultation | ≈ 5½ minutes to transcript-with-speakers |
| a 30-minute consultation | ≈ 11 minutes |
| note generation on top | well under a minute |

One recording is processed at a time on purpose: both engines are threaded across the whole CPU
allocation, so a second concurrent job makes both slower rather than one faster.

---

## Recordings, transcripts and notes

| | kept until | deleted by |
|---|---|---|
| the recording (the PHI) | the retention you chose at deploy — 0 h, 24 h, 7 d, 30 d, or indefinitely | a sweep every 15 minutes and once at startup, or deleting its transcript |
| the transcript | you delete it | `DELETE` on the transcript, which **also deletes the recording** |
| the note | you delete it | one at a time — a signed note is a clinical record |

Everything is on the deployment's own volume. **Unpublishing stops the gateway serving the
hostnames and deletes the workloads; it does not delete the volume.** All three survive an
unpublish and are removed when the deployment is deleted.

Upstream holds transcripts and notes in memory, so a pod restart loses every draft a clinician has
written while the recording — the sensitive artefact — persists on disk. That is the wrong way
round, and both are persisted here.

---

## Transcripts you already have

The ordinary path is an upload. The optional **Consultation transcripts** data slot is for the other
case: consultations already transcribed by another system, held by a data owner who will not hand
them over on a promise. Through the marketplace gatekeeper they do not have to — they approve *this
deployment* against the evidence it published, serve it the rows, and withdraw access the moment
that evidence changes. A note is then drafted inside the enclave from a consultation whose audio
never existed here.

Their speaker labels are theirs: the deployment shows them as theirs, names the source, and does
not re-separate or second-guess them.

**One platform limit, stated plainly.** The gatekeeper speaks ClickHouse over HTTP and nothing else,
so a transcript corpus held as rows can be gated and **an audio corpus in object storage cannot** —
whatever a dataset's access mode says. Recordings therefore reach this deployment by upload, under
your own control, and are never pulled from anywhere.

---

## Same open reference architecture. Run where your data is allowed to be.

We take NVIDIA's open-source reference architectures and run them inside confidential hardware.
Three things the original does not give you: the data is processed where neither we nor the cloud
operator can see it; every deployment publishes cryptographic proof of exactly which code and which
model are touching it; and the data owner approves that exact build and can cut access the moment it
changes.

**What we give up by dropping NVIDIA's NIM containers:** NVIDIA's tuned serving stack and its
throughput, NVIDIA's enterprise support and release cadence, and a few NVIDIA-only components
(speech, safety models) that we replace with open equivalents. **What that buys:** no NVIDIA AI
Enterprise licence — about $4,500 per GPU per year — no closed container in the middle of a system
whose whole point is that you can verify what runs, and a footprint of one card instead of five.

This is our port, maintained by us. It is not an NVIDIA product and is not endorsed by NVIDIA.

---

## A demonstration recording

Upstream's own walkthrough uses [`yfyeung/medical`](https://huggingface.co/datasets/yfyeung/medical)
— simulated patient-physician conversations, **CC-BY-4.0**, so usable commercially with attribution.
Nothing from it ships in this listing or in the image; it is a suggestion for trying the deployment
with audio that is not a real patient's.
