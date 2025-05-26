import json
import pysrt
import spacy
from dataclasses import dataclass
from typing import List, Optional, Callable, Dict, Any
from collections import Counter

@dataclass
class SegmentConfig:
    """Konfiguration for segmentering"""
    frame_duration_ms: int = 40  # 25 fps = 40ms per frame
    min_gap_frames: int = 4  # Minimum 4 frames mellem undertekster
    merge_threshold_sec: float = 7.0  # Standardværdi for sammenslåning af undertekster
    max_subtitle_duration_sec: float = 7.0  # Maksimal varighed for en undertekst
    
    @property
    def min_gap_ms(self) -> int:
        return self.frame_duration_ms * self.min_gap_frames


def attach_punctuation(words: List[tuple]) -> List[str]:
    """Fjerner mellemrum før tegnsætning"""
    result = []
    current_word = ""
    
    for word, attaches_to in words:
        if attaches_to == "previous":
            if current_word:
                current_word += word
            else:
                if result:
                    result[-1] += word
                else:
                    result.append(word)
        else:
            if current_word:
                result.append(current_word)
            current_word = word
    
    if current_word:
        result.append(current_word)
        
    return result


class SRTGenerator:
    def __init__(self, config: Optional[SegmentConfig] = None):
        self.config = config or SegmentConfig()
        self._raw_results = None
        try:
            self.nlp = spacy.load("da_core_news_sm")  # Dansk sprogmodel
        except OSError:
            try:
                self.nlp = spacy.load("en_core_web_sm")  # Fallback til engelsk model
            except OSError:
                print("Advarsel: Ingen sprogmodel tilgængelig - vil bruge simpel opdeling")
                self.nlp = None

    def generate_metadata_subtitle(self, json_data: Dict[str, Any]) -> pysrt.SubRipItem:
        """Genererer undertekst med metadata"""
        job_data = json_data.get("job", {})
        metadata = json_data.get("metadata", {})
        language_info = metadata.get("language_identification", {})
        transcription_config = metadata.get("transcription_config", {})

        null_text = [
            f"File: {job_data.get('data_name', 'Unknown')}",
            f"Language: {language_info.get('predicted_language', 'Unknown')}",
            f"Configuration: Enhanced={transcription_config.get('operating_point', 'Unknown')}"
        ]
        
        return pysrt.SubRipItem(
            index=1,
            start=pysrt.SubRipTime(0, 0, 0, 0),
            end=pysrt.SubRipTime(0, 0, 0, 320),  # 8 frames ved 25 FPS
            text="\n".join(null_text)
        )

    def build_text_from_timings(self, timings: List[Dict]) -> str:
        """Byg tekst fra timing data med korrekt tegnsætning og mellemrum"""
        text = []
        for i, timing in enumerate(timings):
            if timing["type"] == "word":
                needs_space = False
                
                # Tilføj mellemrum hvis:
                # 1. Det ikke er første ord
                # 2. Det forrige element var et komma eller andet tegn der kræver mellemrum efter
                # 3. Ordet ikke starter efter en bindestreg
                if len(text) > 0:
                    prev_was_comma = (
                        i > 0 and 
                        timings[i-1]["type"] == "punctuation" and 
                        timings[i-1]["word"] in [",", ";", ":", "."]
                    )
                    # Altid mellemrum efter komma, men ellers kun hvis ikke sidste endte med bindestreg
                    if prev_was_comma or (not text[-1].endswith("-")):
                        needs_space = True
                
                word = " " + timing["word"] if needs_space else timing["word"]
                text.append(word)
                
            else:  # punctuation
                # For tegnsætning, tilføj direkte til sidste ord uden mellemrum
                if text:
                    text[-1] = text[-1] + timing["word"]
                else:
                    # Hvis der ikke er noget ord at tilføje til, opret nyt element
                    # MEN, vi vil ikke have punktum i starten
                    if timing["word"] not in [".", "!"]:
                        text.append(timing["word"])
        
        result = "".join(text)
        print(f"      DEBUG: Built text: '{result}'")  # Debug output
        return result

    def _find_split_candidates(self, timings: List[Dict]) -> List[int]:
        """Find alle mulige split points sorteret efter prioritet"""
        total_duration = timings[-1]["end"] - timings[0]["start"]
        min_segment_duration = 2.0  # Minimum 2 sekunder per segment
        candidates = []
        
        # Find alle kommaer der giver fornuftige splits
        for i, timing in enumerate(timings[:-1]):  # Undgå sidste element
            # Kun check på kommaer
            if timing["type"] == "punctuation" and timing["word"] == ",":
                # Find det næste ord efter kommaet
                next_word_idx = None
                for j in range(i + 1, len(timings)):
                    if timings[j]["type"] == "word":
                        next_word_idx = j
                        break
                
                if next_word_idx is None:
                    continue
                    
                # Check varigheder før og efter dette komma
                left_duration = timing["end"] - timings[0]["start"]
                right_duration = timings[-1]["end"] - timings[next_word_idx]["start"]
                
                if (left_duration >= min_segment_duration and 
                    right_duration >= min_segment_duration):
                    # Gem indexet for ordet efter kommaet
                    candidates.append(next_word_idx)
        
        # Hvis vi ikke har nok kommaer og har Spacy, find syntaktiske splits
        if len(candidates) < 1 and self.nlp:
            text = " ".join(t["word"] for t in timings if t["type"] == "word")
            doc = self.nlp(text)
            
            # Byg mapping mellem tokens og timing indices
            token_to_idx = {}
            word_count = 0
            for i, t in enumerate(timings):
                if t["type"] == "word":
                    token_to_idx[word_count] = i
                    word_count += 1
            
            # Find potentielle splits ved præpositioner og ledsætninger
            for token in doc:
                if token.i not in token_to_idx:
                    continue
                
                timing_idx = token_to_idx[token.i]
                
                # Undgå splits tæt på eksisterende
                if any(abs(timing_idx - c) < 3 for c in candidates):
                    continue
                
                # Check kun præpositioner og ledsætningsmarkører
                if token.dep_ in ["prep", "mark"]:
                    left_duration = timings[timing_idx]["end"] - timings[0]["start"]
                    right_duration = timings[-1]["end"] - timings[timing_idx]["start"]
                    
                    if (left_duration >= min_segment_duration and 
                        right_duration >= min_segment_duration):
                        candidates.append(timing_idx)
        
        # Sortér kandidater efter position (bagfra)
        return sorted(candidates, reverse=True)

    def find_split_points(self, timings: List[Dict], max_duration: float) -> List[int]:
        """Find optimale split points for at holde segmenter under max_duration"""
        total_duration = timings[-1]["end"] - timings[0]["start"]
        if total_duration <= max_duration:
            return []
            
        print(f"      DEBUG: Finding splits for duration {total_duration:.1f}s (max {max_duration:.1f}s)")
        
        # Find alle potentielle split points
        candidates = self._find_split_candidates(timings)
        if not candidates:
            print("      DEBUG: No candidates found")
            return []
        
        # Find nødvendige splits
        splits = []
        current_start = 0
        
        for split_idx in candidates:
            # Check varigheden af segmentet fra current_start til split
            if split_idx <= current_start:
                continue
                
            segment_duration = timings[split_idx]["end"] - timings[current_start]["start"]
            print(f"      DEBUG: Checking segment {current_start} to {split_idx}: {segment_duration:.1f}s")
            
            if segment_duration > max_duration:
                # Hvis dette segment er for langt, brug forrige split
                if splits:
                    current_start = splits[-1]
                continue
            
            # Check varigheden af det resterende segment
            remaining_duration = timings[-1]["end"] - timings[split_idx]["end"]
            if remaining_duration <= max_duration:
                # Dette split giver to gode segmenter
                splits.append(split_idx)
                print(f"      DEBUG: Added split at {split_idx}")
                break
            
            # Ellers tilføj dette split og fortsæt
            splits.append(split_idx)
            current_start = split_idx
            print(f"      DEBUG: Added intermediate split at {split_idx}")
        
        print(f"      DEBUG: Final splits: {splits}")
        return splits

    def process_results(self, results: List[Dict]) -> List[pysrt.SubRipItem]:
        """Behandler resultater fra JSON og laver undertekster"""
        self._raw_results = results.copy()
        srt_items = []
        current_block = []
        block_start_time = None
        speaker_counts = Counter()
        
        for item in results:
            if item["type"] == "word":
                word_data = item.get("alternatives", [{}])[0]
                word = word_data.get("content", "")
                attaches_to = item.get("attaches_to", None)
                
                speaker = word_data.get("speaker", "Unknown")
                current_block.append((word, attaches_to))
                speaker_counts[speaker] += 1
                
                if block_start_time is None:
                    block_start_time = item.get("start_time", 0)
                    
            elif item["type"] == "punctuation":
                if current_block:
                    current_block.append((item["alternatives"][0]["content"], "previous"))
                else:
                    current_block.append((item["alternatives"][0]["content"], None))
                    
            if item.get("is_eos"):
                if current_block and block_start_time is not None:
                    dominant_speaker = speaker_counts.most_common(1)[0][0] if speaker_counts else "Unknown"
                    block_text = " ".join(attach_punctuation(current_block))
                    
                    srt_item = pysrt.SubRipItem(
                        index=len(srt_items) + 2,  # +2 fordi metadata er index 1
                        start=pysrt.SubRipTime.from_ordinal(int(block_start_time * 1000)),
                        end=pysrt.SubRipTime.from_ordinal(int(item.get("end_time", 0) * 1000)),
                        text=block_text
                    )
                    srt_item.speaker = dominant_speaker
                    srt_items.append(srt_item)
                
                current_block = []
                block_start_time = None
                speaker_counts.clear()
        
        return srt_items

    def split_long_subtitles(self, srt_items: List[pysrt.SubRipItem]) -> List[pysrt.SubRipItem]:
        """Del lange undertekster i mindre blokke baseret på kommaer og syntaks"""
        if not self._raw_results:
            print("### Ingen raw_results tilgængelige")
            return srt_items
                
        new_items = []
        
        # Først bygger vi en komplet liste af alle ord og deres timings
        print("\n### Bygger timing data...")
        all_timings = []
        for item in self._raw_results:
            if item["type"] == "word" or item["type"] == "punctuation":
                timing = {
                    "word": item["alternatives"][0]["content"],
                    "start": item["start_time"],
                    "end": item.get("end_time", item["start_time"]),
                    "type": item["type"]
                }
                if item.get("attaches_to") == "previous":
                    timing["attaches_to"] = "previous"
                all_timings.append(timing)
        
        print(f"Byggede {len(all_timings)} timing elementer")
        
        # Process each subtitle
        for item in srt_items:
            if item.index == 1:  # Keep metadata
                new_items.append(item)
                continue
            
            duration_sec = (item.end.ordinal - item.start.ordinal) / 1000.0
            print(f"\nUndertekst {item.index}: '{item.text[:50]}...' ({duration_sec:.1f} sek)")
            
            if duration_sec <= self.config.max_subtitle_duration_sec:
                print(f"  Undertekst er kort nok ({duration_sec:.1f} ≤ {self.config.max_subtitle_duration_sec:.1f})")
                new_items.append(item)
                continue
                
            print(f"  Undertekst er for lang ({duration_sec:.1f} > {self.config.max_subtitle_duration_sec:.1f})")
            
            # Find alle timings der falder inden for denne underteksts tidsinterval
            subtitle_start = item.start.ordinal / 1000.0  # Konverter til sekunder
            subtitle_end = item.end.ordinal / 1000.0
            
            segment_timings = []
            for timing in all_timings:
                if (timing["start"] >= subtitle_start - 0.1 and  # Tillad 100ms tolerance
                    timing["end"] <= subtitle_end + 0.1):
                    segment_timings.append(timing)
            
            if not segment_timings:
                print("  ADVARSEL: Kunne ikke finde timing data for teksten!")
                new_items.append(item)
                continue
            
            print(f"  Fandt {len(segment_timings)} timing elementer mellem {subtitle_start:.2f}s og {subtitle_end:.2f}s")
            split_points = self.find_split_points(segment_timings, self.config.max_subtitle_duration_sec)
            print(f"  Fandt {len(split_points)} split points: {split_points}")
            
            if not split_points:
                new_items.append(item)
                continue
                
            # Del teksten ved alle split points
            start_idx = 0
            split_points = sorted(split_points)  # Sikr at punkterne er i rækkefølge
                
            for i, split_idx in enumerate(split_points):
                if split_idx <= start_idx:
                    continue
                    
                segment = segment_timings[start_idx:split_idx]
                if not segment:  # Sikr at vi har noget at arbejde med
                    continue
                    
                text = self.build_text_from_timings(segment)
                segment_duration = segment[-1]["end"] - segment[0]["start"]
                print(f"  Deler segment {i+1}: '{text}' ({segment_duration:.1f} sek)")
                    
                # Tilføj bindestreger for fortsættelse
                if start_idx > 0:
                    text = "- " + text
                    
                # Hvis teksten skal slutte med en bindestreg og den slutter med et komma,
                # fjern kommaet før bindestegen tilføjes
                if (i < len(split_points) or split_idx < len(segment_timings)):
                    if text.endswith(","):
                        text = text[:-1]  # Fjern kommaet
                    text += " -"
                    
                start_time = segment[0]["start"]
                end_time = segment[-1]["end"]
                    
                # Sikr at timing er inden for original underteksts grænser
                start_time = max(start_time, subtitle_start)
                end_time = min(end_time, subtitle_end)
                    
                new_item = pysrt.SubRipItem(
                    index=len(new_items) + 1,
                    start=pysrt.SubRipTime(milliseconds=int(start_time * 1000)),
                    end=pysrt.SubRipTime(milliseconds=int(end_time * 1000)),
                    text=text
                )
                    
                if hasattr(item, 'speaker'):
                    new_item.speaker = item.speaker
                    
                new_items.append(new_item)
                start_idx = split_idx
            
            # Håndter sidste segment hvis nødvendigt
            if start_idx < len(segment_timings):
                segment = segment_timings[start_idx:]
                if segment:  # Sikr at vi har noget at arbejde med
                    text = self.build_text_from_timings(segment)
                    segment_duration = segment[-1]["end"] - segment[0]["start"]
                    print(f"  Sidste segment: '{text}' ({segment_duration:.1f} sek)")
                    
                    # Tilføj bindestreger for sidste del
                    if start_idx > 0:
                        text = "- " + text
                        
                    start_time = segment[0]["start"]
                    end_time = segment[-1]["end"]
                        
                    # Sikr at timing er inden for original underteksts grænser
                    start_time = max(start_time, subtitle_start)
                    end_time = min(end_time, subtitle_end)
                        
                    new_item = pysrt.SubRipItem(
                        index=len(new_items) + 1,
                        start=pysrt.SubRipTime(milliseconds=int(start_time * 1000)),
                        end=pysrt.SubRipTime(milliseconds=int(end_time * 1000)),
                        text=text
                    )
                        
                    if hasattr(item, 'speaker'):
                        new_item.speaker = item.speaker
                        
                    new_items.append(new_item)
            
        # Renummerér undertekster
        for i, item in enumerate(new_items, start=1):
            item.index = i
            
        return new_items
    
    def merge_subtitles(self, srt_items: List[pysrt.SubRipItem]) -> List[pysrt.SubRipItem]:
        """Slår korte undertekster sammen"""
        if not srt_items:
            return []
            
        merged_items = [srt_items[0]]  # Behold metadata
        
        for item in srt_items[1:]:
            prev_item = merged_items[-1]
            
            same_speaker = (
                hasattr(item, 'speaker') and 
                hasattr(prev_item, 'speaker') and 
                item.speaker == prev_item.speaker
            )
            
            gap_ms = item.start.ordinal - prev_item.end.ordinal
            duration_sec = (item.end.ordinal - prev_item.start.ordinal) / 1000
            
            if same_speaker and duration_sec <= self.config.merge_threshold_sec:
                prev_item.text = f"{prev_item.text} {item.text}"
                prev_item.end = item.end
            else:
                merged_items.append(item)
        
        # Renummerér undertekster
        for i, item in enumerate(merged_items, start=1):
            item.index = i
            
        return merged_items

    def extend_subtitle_end_time(self, srt_items: List[pysrt.SubRipItem], max_extension_sec: float = 1.0) -> List[pysrt.SubRipItem]:
        """Forlænger udtiden og sikrer 4-frames mellemrum mellem tekster."""
        min_gap_ms = self.config.min_gap_ms
        max_gap_ms = 1000  # 25 frames

        for i in range(len(srt_items) - 1):
            current = srt_items[i]
            next_item = srt_items[i + 1]

            extended_end_ms = current.end.ordinal + int(max_extension_sec * 1000)
            max_allowed_end_ms = next_item.start.ordinal - min_gap_ms
            current_gap = next_item.start.ordinal - current.end.ordinal

            if current_gap < min_gap_ms:
                current.end = pysrt.SubRipTime(milliseconds=next_item.start.ordinal - min_gap_ms)
            elif min_gap_ms <= current_gap <= max_gap_ms:
                current.end = pysrt.SubRipTime(milliseconds=next_item.start.ordinal - min_gap_ms)
            elif extended_end_ms <= max_allowed_end_ms:
                current.end = pysrt.SubRipTime(milliseconds=extended_end_ms)

        return srt_items


def segment_json(json_data: Dict[str, Any],
                config: Optional[Dict] = None,
                progress_callback: Optional[Callable[[str], None]] = None) -> Optional[List[pysrt.SubRipItem]]:
    """
    Segmenterer JSON til SRT.
    """
    try:
        if progress_callback:
            progress_callback("Starter segmentering af JSON...")
            
        generator = SRTGenerator(SegmentConfig(**(config or {})))

        if progress_callback:
            progress_callback("Genererer metadata...")
        srt_items = [generator.generate_metadata_subtitle(json_data)]

        if progress_callback:
            progress_callback("Behandler resultater...")
        results = json_data.get("results", [])
        srt_items.extend(generator.process_results(results))

        if progress_callback:
            progress_callback("Slår korte undertekster sammen...")
        srt_items = generator.merge_subtitles(srt_items)

        if progress_callback:
            progress_callback("Deler lange undertekster...")
        srt_items = generator.split_long_subtitles(srt_items)

        if progress_callback:
            progress_callback("Forlænger udtider for undertekster...")
        srt_items = generator.extend_subtitle_end_time(srt_items)

        if progress_callback:
            progress_callback("Segmentering færdig")
            
        return srt_items
        
    except Exception as e:
        if progress_callback:
            progress_callback(f"Fejl under segmentering: {str(e)}")
        return None


if __name__ == "__main__":
    import sys
    
    def print_progress(msg: str):
        print(msg)
    
    if len(sys.argv) != 3:
        print("Brug: python dr_segment.py <input.json> <output.srt>")
        sys.exit(1)
        
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    srt_items = segment_json(json_data, progress_callback=print_progress)
    
    if srt_items:
        subs = pysrt.SubRipFile(srt_items)
        subs.save(sys.argv[2], encoding='utf-8')
        print(f"\nUndertekster gemt i: {sys.argv[2]}")
    else:
        print("\nFejl under generering af undertekster")