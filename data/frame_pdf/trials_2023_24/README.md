# ADCC Trials Finals 2023-24 — frame sheets (v2, curated)

52 bout PDFs rendered from https://www.youtube.com/watch?v=pwGbW5GZgfc (8h29m33s),
one per bout actually present in the compilation, every athlete NAMED. Replaces the
v1 set (6 header-named bouts + 50 blind 600s windows) that split fights across files.

Provenance: `trials_2023_24_bouts.json` — corner-scoreboard read of every frame of the
v1 PDFs (436 pages, 6 vision passes) cross-referenced against the published finals of
all 8 trials events in the 2023-24 cycle (bjjheroes/jitsmagazine). Boundaries +-20s,
padded to include walkout and celebration. Regenerate: `uv run python
scripts/build_trials_manifest.py` then `uv run python scripts/frame_pdf.py --manifest
data/frame_pdf/trials_2023_24_manifest.json --format pdf --force`.

Every frame carries VIDEO-ABSOLUTE time + the narration transcribed in its 5s window.
Reading rules: docs/frame_pdf_reading.md.

## Bouts

### EU/ME/Africa Trials 2023 (Warsaw)

- +99kg: **Heikki Jussila vs. Daniel Manasoiu** (0:00:00–0:13:50)
- 99kg: **Luke Griffith vs. Mraz Avdoyan** (0:13:30–0:15:55) — short clip in compilation
- 88kg: **Santeri Lilius vs. Tuomas Simola** (0:15:50–0:24:10)
- 77kg: **Jozef Chen vs. Oliver Taza** (0:24:10–0:38:50)
- 66kg: **Owen Jones vs. Cammy Donnelly** (0:38:50–0:48:10)

### East Coast Trials 2023 (Atlantic City)

- +99kg: **Daniel Manasoiu vs. Damon Ramos** (0:50:00–0:57:20)
- 99kg: **Paul Ardila vs. Alex Grandy** (0:59:20–1:04:40)
- 88kg: **Jacob Couch vs. Elder Cruz** (1:04:30–1:11:20)
- 77kg: **Elijah Dorsey vs. Nicky Ryan** (1:12:50–1:27:50)
- 66kg: **Dorian Olivarez vs. Dominic Mejia** (1:28:40–1:40:40)

### Asia & Oceania Trials 2023 (Singapore)

- +99kg: **Josh Saunders vs. Ricky Luzny** (1:40:40–1:45:50)
- 99kg: **Declan Moody vs. Anton Minenko** (1:45:50–1:54:50)
- 88kg: **Izaak Michell vs. Roberto Dib** (1:54:50–2:04:30)
- 77kg: **Kenta Iwamoto vs. Rhys Allan** (2:04:30–2:13:50)
- 66kg: **Ethan Thomas vs. Minoru Takeuchi** (2:13:50–2:21:30)

### South American Trials 1 2024 (Belo Horizonte)

- +99kg: **Inacio Santos vs. Antonio Assef** (2:21:30–2:30:30)
- 99kg: **Felipe Costa vs. Elionai Braz** (2:30:30–2:39:10)
- 88kg: **Charles Negromonte vs. Gabriel Almeida** (2:39:10–2:52:30)
- 77kg: **Luiz Paulo vs. Jefferson Pontes** (2:52:30–2:58:50)
- 66kg: **Kennedy Maciel vs. Kaua Gabriel** (2:58:50–3:07:10)

### South American Trials 2 2024 (Sao Paulo)

- 55kg F: **Anna Rodrigues vs. Franciele Santos** (3:07:10–3:22:25)
- 65kg F: **Ana Vieira vs. Gabrielle McComb** (3:22:20–3:29:35)
- +65kg F: **Maria Ruffatto vs. Kauane Silva** (3:29:30–3:33:20)
- +99kg: **Victor Honorio vs. Matheus Lino** (3:33:20–3:42:40)
- 99kg: **Henrique Cardoso vs. Elionai Braz** (3:42:40–3:52:20)
- 88kg: **Pedro Marinho vs. Gabriel Almeida** (3:52:20–4:11:40) — blood stoppage mid-match
- 77kg: **Alexandre Jesus vs. Jonnatas Gracie** (4:11:40–4:25:50)
- 66kg: **Fabricio Andrey vs. Kaua Gabriel** (4:25:50–4:40:30)

### EU/Africa/ME Trials 2024 (Zagreb)

- 55kg F: **Margot Ciccarelli vs. Ashley Bendle** (4:40:30–4:54:20)
- 65kg F: **Aurelie Le Vern vs. Nadia Frankland** (4:54:20–4:55:30) — SEMIFINAL (armbar) - the Le Vern vs Schultz final is not in the compilation
- +65kg F: **Nia Blackman vs. Salla Simola** (4:55:30–5:09:20)
- +99kg: **Mark Macqueen vs. Freddy Vosgroene** (5:09:20–5:18:10)
- 99kg: **Marcin Maciulewicz vs. Kasper Larsen** (5:18:10–5:22:10)
- 88kg: **Taylor Pearman vs. Ben Bennett** (5:22:00–5:26:50)
- 77kg: **Tommy Langaker vs. Davis Asare** (5:26:40–5:34:10)
- 66kg: **Gairbeg Ibragimov vs. Yigit Hanay** (5:34:00–5:47:50)

### West Coast Trials 2024 (Las Vegas)

- 55kg F: **Jasmine Rocha vs. Alex Enriquez** (5:47:40–6:00:05)
- 65kg F: **Helena Crevar vs. Morgan Black** (6:00:00–6:12:00)
- +65kg F: **Elizabeth Mitrovic vs. Amanda Leve** (6:12:00–6:26:00)
- +99kg: **Michael Perez vs. Michael Pezzuto** (6:26:00–6:42:00)
- 99kg: **Michael Pixley vs. Adam Bradley** (6:42:00–6:57:00)
- 88kg: **William Tackett vs. Jacob Rodriguez** (6:58:20–7:03:50)
- 77kg: **Andrew Tackett vs. Oliver Taza** (7:05:40–7:14:40)
- 66kg: **Deandre Corbe vs. Keith Krikorian** (7:18:10–7:26:30)

### Asia & Oceania Trials 2 2024 (Bangkok)

- 55kg F: **Adele Fornarino vs. Kanae Yamada** (7:28:00–7:36:20)
- 65kg F: **Sula Loewenthal vs. Nadia Frankland** (7:36:30–7:45:20)
- +65kg F: **Nikki Lloyd-Griffiths vs. Gase Sanita** (7:45:20–7:50:40)
- +99kg: **Mansur Makhmakhanov vs. Dalton Terei** (7:50:40–7:53:50)
- 99kg: **Daniel Schuardt vs. Alibi Orazbek** (7:53:50–8:02:40)
- 88kg: **Lucas Kanard vs. William Dias** (8:02:50–8:06:40)
- 77kg: **Levi Jones-Leary vs. Jeremy Skinner** (8:06:40–8:15:00)
- 66kg: **Xu Huaiqing vs. Daiki Yonekura** (8:15:00–8:28:20)
