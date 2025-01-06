import json
import pysrt
from dataclasses import dataclass
from typing import List, Optional, Callable, Dict, Any
from collections import Counter

@dataclass
class SegmentConfig:
    """Konfiguration for segmentering"""
    frame_duration_ms: int = 40  # 25 fps = 40ms per frame
    min_gap_frames: int = 4  # Minimum 4 frames mellem undertekster
    merge_threshold_sec: float = 7.0  # Standardværdi for sammenslåning af undertekster
    
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

    def process_results(self, results: List[Dict]) -> List[pysrt.SubRipItem]:
        """Behandler resultater fra JSON og laver undertekster"""
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
        
        # Renumber subtitles
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
