# OpportunityMap

Turns a client **discovery-call transcript** into a ranked, evidence-backed
**automation opportunity map** with transparent ROI, in about a minute.

* The AI (or a rule-based fallback) only **extracts** facts.
* Plain Python does the **ROI math**, so every number is traceable.
* Every opportunity is linked to a **verbatim quote**, checked against the transcript.
* A human can **edit assumptions**, and the map updates instantly.

**Demo login:** `demo` / `demo123`

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Works immediately in **demo mode** (no key needed).

## Files
| File | Purpose |
|---|---|
| `app.py` | Streamlit interface, login, tabs, downloads |
| `engine.py` | Extraction (AI + fallback), ROI math, report builder |
| `sample_transcripts/` | 3 fictional discovery calls for the demo |
| `architecture.mmd` | Mermaid architecture diagram |
| `requirements.txt` | Python dependencies |

## Honest limitations
* The rule-based fallback is keyword driven and is a demo of the pipeline, not a replacement for the AI mode.
* ROI figures are estimates from what the client said, plus clearly flagged defaults.
* Audio upload (faster-whisper) is planned for Phase 2.
