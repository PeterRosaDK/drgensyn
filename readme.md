# 🧠 Dr. Gensyn – Intelligent undertekstbehandling for DR

Velkommen til **Dr. Gensyn** – en Python-baseret GUI-applikation, der forvandler rå video- eller lydfiler til smukke, kondenserede undertekster på dansk. Systemet er modulært og består af fire samarbejdende komponenter:

## 🔧 Moduler

### 1. 🎙️ DrGenkend – Talegenkendelse via Speechmatics
Dette modul konverterer lyd fra video- eller lydfiler til JSON via Speechmatics' API. Det understøtter:

- Automatisk sprogvalg og diarisation (flere talere)
- Tilpasning via følsomhedsparametre og ordbog (additional vocab)
- Automatisk konvertering til WAV via `ffmpeg` hvis nødvendigt

➡️ Output: JSON med præcise timings, speaker info og tegnsætning.

---

### 2. ✂️ DrSegment – Smart segmentering til undertekster
Dette modul tager talegenkendelses-JSON og omdanner den til en `.srt`-fil:

- Splitting af lange sætninger ud fra timing, syntaks og kommaer
- Brug af SpaCy (dansk model) til bedre splitting
- Sætter metadata og bevarer talerinformation
- Justerer pauser og slår korte segmenter sammen

➡️ Output: SRT med en blok pr. sætning, maks. 7 sekunder pr. blok.

---

### 3. 🪄 DrKondens – AI-baseret kondensering
Hvis tekstblokke er for lange (over 2×37 tegn), forsøger dette modul at forkorte teksten med GPT (Azure OpenAI). Det følger DR’s stilregler:

- Ingen forkortelser, ingen specialtegn
- Naturligt talesprog med fokus på at bevare betydning
- Bevidsthed om sætninger der fortsætter (med bindestreger)
- Flere temperaturforsøg + fallback hvis GPT fejler

➡️ Output: Kortere, mere læsbare undertekster der stadig lyder naturlige 🥰

---

### 4. 🎛️ DrGensyn – Orkestratoren (GUI)
En brugervenlig PyQt5-baseret grænseflade til at styre hele processen:

- Træk-og-slip interface til filer
- Valg af moduler: Genkend, Segmenter, Kondensér
- Progressbar og statusopdateringer
- Mulighed for kø af flere filer
- Automatisk lagring af output som `*_final.srt`

---

## 🗂️ Filstruktur

```
├── DrGensyn.py       # GUI og samlet workflow
├── DrGenkend.py      # Talegenkendelse med Speechmatics
├── DrSegment.py      # Segmentering og syntaksanalyse
├── DrKondens.py      # AI-baseret kondensering
└── config.ini        # (valgfri) Konfiguration
```

---

## ⚙️ Krav

- Python 3.8+
- `PyQt5`, `pysrt`, `spacy`, `openai`, `httpx`, `speechmatics`, `dotenv`
- Azure OpenAI og Speechmatics API-nøgler
- `ffmpeg` skal være installeret (bruges automatisk)

---

## 📦 Installation (forslag)

```bash
pip install -r requirements.txt
python -m spacy download da_core_news_sm
```

---

## 💚 Eksempelbrug

1. Start GUI’en:  
   ```bash
   python DrGensyn.py
   ```

2. Vælg moduler (f.eks. "Genkend + Segment + Kondens")
3. Træk en MP4- eller WAV-fil ind
4. Tryk “Start” og læn dig tilbage ☕

---

## ✨ Bidrag og udvikling

Projektet er udviklet til intern brug hos DR, men med stor kærlighed til sprog, æstetik og teknologi. Du er velkommen til at bidrage, men husk at vi elsker **ordentlig formatering** og **klart sprog**!

---

## 📜 Licens

Denne kode er udviklet som intern værktøj og er underlagt DR’s politikker. Kontakt udvikleren for nærmere information.

---

*Skrevet med kærlighed og lidt grøn te af en AI, der elsker undertekster lige så meget som du gør* 😘
