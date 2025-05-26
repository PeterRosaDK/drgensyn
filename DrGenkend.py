import os
import json
import subprocess
import logging
import sys
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from speechmatics.models import ConnectionSettings
from speechmatics.batch_client import BatchClient
from httpx import HTTPStatusError

# Opsæt logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('drgenkend.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('DrGenkend')

@dataclass
class RecognitionConfig:
    """Konfiguration for talegenkendelse"""
    api_key: str
    language: str = "da"
    operating_point: str = "enhanced"
    diarization: str = "speaker"
    enable_entities: bool = True
    permitted_marks: list = None
    additional_vocab: list = None
    merge_threshold_sec: Optional[float] = None  # Tilføj det nye argument
    speaker_sensitivity: Optional[float] = None
    punctuation_sensitivity: Optional[float] = None
    volume_threshold: Optional[float] = None
    
    def __post_init__(self):
        if self.permitted_marks is None:
            self.permitted_marks = [",", ".", "?"]
    
    def to_dict(self) -> dict:
        """Konverterer config til Speechmatics format"""
        transcription_config = {
            "language": self.language,
            "operating_point": self.operating_point,
            "diarization": self.diarization,
            "enable_entities": self.enable_entities,
            "punctuation_overrides": {
                "permitted_marks": self.permitted_marks
            }
        }

        # Tilføj speaker sensitivity hvis sat
        if self.diarization == "speaker" and self.speaker_sensitivity is not None:
            transcription_config["speaker_diarization_config"] = {
                "speaker_sensitivity": self.speaker_sensitivity
            }

        # Tilføj punctuation sensitivity hvis sat
        if self.punctuation_sensitivity is not None:
            if "punctuation_overrides" not in transcription_config:
                transcription_config["punctuation_overrides"] = {}
            transcription_config["punctuation_overrides"]["sensitivity"] = self.punctuation_sensitivity

        # Tilføj volume threshold hvis sat
        if self.volume_threshold is not None:
            transcription_config["audio_filtering_config"] = {
                "volume_threshold": self.volume_threshold
            }

        # Tilføj ordbog hvis sat
        if self.additional_vocab:
            transcription_config["additional_vocab"] = self.additional_vocab

        logger.debug(f"Final transcription config: {transcription_config}")

        return {
            "type": "transcription",
            "transcription_config": transcription_config
        }


class AudioConverter:
    """Håndterer konvertering af video/lyd til WAV format"""
    @staticmethod
    def find_ffmpeg() -> Optional[str]:
        logger.debug("Søger efter ffmpeg...")
        # Først tjek system PATH
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            logger.info("ffmpeg fundet i system PATH")
            return "ffmpeg"
        except:
            # Så tjek forskellige mulige placeringer
            possible_paths = [
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg", "bin", "ffmpeg.exe"),  # Relativ til script
                os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe"),  # Relativ til arbejdsmappe
                os.path.join(getattr(sys, '_MEIPASS', os.path.abspath(".")), "ffmpeg", "bin", "ffmpeg.exe")  # PyInstaller support
            ]
            
            for path in possible_paths:
                logger.debug(f"Tjekker for ffmpeg i: {path}")
                if os.path.exists(path):
                    logger.info(f"ffmpeg fundet i: {path}")
                    return path
                    
            logger.error("ffmpeg ikke fundet")
            return None

    @staticmethod
    def convert_to_wav(input_path: str, progress_callback: Optional[Callable] = None) -> Optional[str]:
        output_path = os.path.splitext(input_path)[0] + ".wav"
        logger.info(f"Konverterer {input_path} til {output_path}")

        if os.path.exists(output_path):
            msg = f"Bruger eksisterende WAV fil: {output_path}"
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)
            return output_path

        ffmpeg_path = AudioConverter.find_ffmpeg()
        if not ffmpeg_path:
            msg = "FEJL: ffmpeg ikke fundet"
            logger.error(msg)
            if progress_callback:
                progress_callback(msg)
            return None

        try:
            command = [
                ffmpeg_path, '-i', input_path,
                '-vn',
                '-acodec', 'pcm_s16le',
                '-ar', '44100',
                '-ac', '2',
                '-y',
                output_path
            ]
            logger.debug(f"ffmpeg kommando: {' '.join(command)}")
            
            if progress_callback:
                progress_callback(f"Konverterer {input_path} til WAV...")
            
            result = subprocess.run(command, capture_output=True, text=True)
            
            if result.returncode == 0:
                msg = "Konvertering gennemført"
                logger.info(msg)
                if progress_callback:
                    progress_callback(msg)
                return output_path
            else:
                msg = f"FEJL under konvertering: {result.stderr}"
                logger.error(msg)
                if progress_callback:
                    progress_callback(msg)
                return None
        except Exception as e:
            msg = f"FEJL: {str(e)}"
            logger.error(msg)
            if progress_callback:
                progress_callback(msg)
            return None

class SpeechRecognizer:
    """Hovedklasse for talegenkendelse"""
    def __init__(self, config: RecognitionConfig):
        self.config = config
        try:
            self.settings = ConnectionSettings(
                url="https://asr.api.speechmatics.com/v2",
                auth_token=config.api_key
            )
            logger.info("SpeechRecognizer initialiseret")
        except Exception as e:
            logger.error(f"Fejl ved initialisering af SpeechRecognizer: {str(e)}")
            raise
    
    def run_recognition(self, input_file: str, progress_callback: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
        """Udfører talegenkendelse med fallback til videofil."""
        try:
            logger.info(f"Starter talegenkendelse af {input_file}")
            if not os.path.exists(input_file):
                msg = f"FEJL: Inputfil ikke fundet: {input_file}"
                logger.error(msg)
                if progress_callback:
                    progress_callback(msg)
                return None

            # Forsøg at konvertere til WAV
            wav_file = None
            if not input_file.lower().endswith('.wav'):
                wav_file = AudioConverter.convert_to_wav(input_file, progress_callback)
            
            # Hvis konvertering fejler, brug originalfilen som fallback
            if not wav_file:
                msg = f"Kunne ikke konvertere {input_file} til WAV. Sender originalfil som fallback."
                logger.warning(msg)
                if progress_callback:
                    progress_callback(msg)
                wav_file = input_file  # Fallback til den originale fil

            if progress_callback:
                progress_callback("Opretter forbindelse til Speechmatics...")
            
            logger.debug("Opretter BatchClient")
            with BatchClient(self.settings) as client:
                # Konfigurer job
                config_dict = self.config.to_dict()
                logger.debug(f"Job konfiguration: {config_dict}")
                
                try:
                    job_id = client.submit_job(
                        audio=wav_file,
                        transcription_config=config_dict
                    )
                    logger.info(f"Job oprettet med ID: {job_id}")
                    if progress_callback:
                        progress_callback(f"Job oprettet med ID: {job_id}")

                    # Vent på resultater
                    logger.info("Venter på resultater...")
                    transcript = client.wait_for_completion(
                        job_id,
                        transcription_format='json-v2'
                    )
                    
                    if progress_callback:
                        progress_callback("Transskription modtaget")
                    
                    logger.info("Transskription modtaget succesfuldt")
                    return transcript

                except HTTPStatusError as e:
                    # Håndter fejl fra API
                    if e.response.status_code == 401:
                        msg = "Ugyldig API nøgle"
                        logger.error(msg)
                        if progress_callback:
                            progress_callback(msg)
                    elif e.response.status_code == 400:
                        msg = f"API Fejl: {e.response.json().get('detail', 'Ukendt fejl')}"
                        logger.error(msg)
                        if progress_callback:
                            progress_callback(msg)
                    else:
                        raise e
                    return None

        except Exception as e:
            msg = f"Uventet fejl: {str(e)}"
            logger.error(msg)
            if progress_callback:
                progress_callback(msg)
            return None

def recognize_speech(input_file: str, config: Dict, progress_callback: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """
    Hovedfunktion for talegenkendelse

    Args:
        input_file: Sti til video/lydfil
        config: Talegenkendelses-konfiguration
        progress_callback: Funktion til statusopdateringer

    Returns:
        dict: JSON-respons fra API'et, eller None hvis fejl
    """
    try:
        logger.info(f"Starter talegenkendelse for {input_file}")
        logger.debug(f"Konfiguration: {config}")
        
        recognition_config = RecognitionConfig(**config)
        recognizer = SpeechRecognizer(recognition_config)
        
        return recognizer.run_recognition(input_file, progress_callback)
        
    except Exception as e:
        msg = f"Fejl i recognize_speech: {str(e)}"
        logger.error(msg)
        if progress_callback:
            progress_callback(msg)
        return None

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    def print_progress(msg: str):
        print(msg)
        logger.info(f"Progress: {msg}")

    logger.info("=== DrGenkend startet fra kommandolinje ===")

    # Indlæs miljøvariabler
    load_dotenv()
    api_key = os.getenv("SPEECHMATICS_API_KEY")
    
    if not api_key:
        logger.error("Ingen API-nøgle fundet i .env")
        print("FEJL: Ingen API-nøgle fundet i .env")
        sys.exit(1)

    if len(sys.argv) != 2:
        logger.error("Forkert antal argumenter")
        print("Brug: python DrGenkend.py <input_file>")
        sys.exit(1)

    # Konfiguration
    config = {
        "api_key": api_key,
        "language": "da",
        "additional_vocab": [
            {"content": "Holm"},
            {"content": "toldassistent", "sounds_like": ["12. assistent"]}
        ]
    }

    # Kør talegenkendelse
    result = recognize_speech(
        input_file=sys.argv[1],
        config=config,
        progress_callback=print_progress
    )

    if result:
        output_file = os.path.splitext(sys.argv[1])[0] + "_transcript.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"Resultat gemt i: {output_file}")
        print(f"Resultat gemt i: {output_file}")
    else:
        logger.error("Fejl under genkendelse")
        print("Fejl under genkendelse")