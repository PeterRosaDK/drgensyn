import os
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass
from openai import AzureOpenAI
from dotenv import load_dotenv

@dataclass
class CondensationConfig:
    """Konfiguration for tekstkondensering"""
    max_chars: int  # Maksimal længde for kondenseret tekst
    model_name: str = "gptx4xo_relaxed"
    temperatures: List[float] = None

    def __post_init__(self):
        if self.temperatures is None:
            self.temperatures = [0.3, 0.7, 1.0]

class TextValidator:
    """Validerer output fra GPT."""
    @staticmethod
    def is_valid(text: str) -> bool:
        """Validerer at teksten overholder reglerne."""
        if "..." in text:
            print(f"Tekst indeholder '...': {text}")
            return False
        if any(char in text for char in ['!', ';']):
            print(f"Tekst indeholder forbudte tegn: {text}")
            return False
        abbreviations = ["f.eks.", "m.m.", "osv.", "dvs.", "bl.a.", "fx", "ca."]
        if any(abbr in text.lower() for abbr in abbreviations):
            print(f"Tekst indeholder forkortelser: {text}")
            return False
        return True

class TextCondenser:
    """Håndterer kondensering af tekst via GPT med kontekstbevidsthed."""
    
    SYSTEM_PROMPT = """Du er ekspert i at forkorte danske undertekster.
    Din opgave er at omskrive teksten til en kortere version der bevarer den væsentlige mening.
    Du skal:
    1. Bevare den oprindelige mening så meget som muligt.
    2. Bruge naturligt dansk talesprog.
    3. Beholde så mange af de oprindelige ord som muligt, så undgå at finde på synonymer.
    4. Aldrig bruge forkortelser eller specialtegn.
    5. Hver undertekst må højst indeholde {max_chars} tegn.
    6. Hvis teksten begynder med "-" betyder det, at det er fortsættelsen af en sætning, så bevar tonen og stil.
    7. Hvis teksten slutter med "-" betyder det, at sætningen fortsætter i næste undertekst, så bevar afslutningen naturlig."""

    def __init__(self, config):
        self.config = config
        load_dotenv()
        self.client = AzureOpenAI(
            azure_endpoint=os.getenv("OPENAI_AZURE_ENDPOINT"),
            api_key=os.getenv("OPENAI_AZURE_API_KEY"),
            api_version="2023-06-01-preview"
        )

    def get_condensation(self, text: str, temperature: float, is_continuation: bool = False, continues: bool = False) -> Optional[str]:
        """Genererer ét kondenseringsforslag fra GPT med bevidsthed om del-sætninger."""
        try:
            target_length = self.config.max_chars
            
            # Tag højde for fortsættelsesstreger i længdeberegning
            adjusted_target = target_length
            if is_continuation:
                adjusted_target -= 2  # For "- " i starten
            if continues:
                adjusted_target -= 2  # For " -" i slutningen
            
            # Lav en justeret tekst uden fortsættelsesstreger til GPT
            clean_text = text
            if is_continuation and text.startswith("- "):
                clean_text = text[2:]
            if continues and text.endswith(" -"):
                clean_text = text[:-2]
                
            print(f"Genererer forslag (temp={temperature}, fortsættelse={is_continuation}, fortsætter={continues})")
            
            prompt = f"""
                Omskriv denne tekst til en kortere version på omkring {adjusted_target} tegn.
                Den må IKKE være længere end {adjusted_target} tegn.
                Bevar den væsentlige mening og brug naturligt dansk sprog.
                
                {f"Dette er fortsættelsen af en sætning, så bevar stil og tone der passer til det." if is_continuation else ""}
                {f"Denne sætning fortsætter i næste undertekst, så afslut på en naturlig måde der viser at sætningen ikke er færdig." if continues else ""}
                
                TEKST: {clean_text}
            """
            
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT.format(max_chars=self.config.max_chars)},
                    {"role": "user", "content": prompt}
                ],
            )
            output = response.choices[0].message.content.strip()
            print(f"GPT output: {output}")
            
            # Tilføj fortsættelsesstreger igen hvis nødvendigt
            if is_continuation and not output.startswith("- "):
                output = "- " + output
            if continues and not output.endswith(" -"):
                output = output + " -"
            
            return output if len(output) <= target_length else None
        except Exception as e:
            print(f"GPT fejl: {e}")
            return None
            
    def strict_fallback(self, text: str, is_continuation: bool = False, continues: bool = False) -> str:
        """Fallback der sikrer kondensering uden '...' og med afrundet mening."""
        # Justér target længde for fortsættelsesstreger
        target_length = self.config.max_chars
        if is_continuation:
            target_length -= 2  # For "- " i starten
        if continues:
            target_length -= 2  # For " -" i slutningen
            
        # Fjern fortsættelsesstreger til processering
        clean_text = text
        if is_continuation and text.startswith("- "):
            clean_text = text[2:]
        if continues and text.endswith(" -"):
            clean_text = text[:-2]
            
        strict_prompt = f"""
        Din opgave er at forkorte følgende tekst til én kort sætning, der bevarer den vigtigste mening. 
        Teksten må IKKE være længere end {target_length} tegn.
        Teksten skal være naturligt afrundet og må ALDRIG slutte med "..." eller føles ufuldstændig.
        
        {f"Dette er fortsættelsen af en sætning, bevar stil/tone der passer til det." if is_continuation else ""}
        {f"Denne sætning fortsætter i næste undertekst, afslut så det er tydeligt at sætningen ikke er færdig." if continues else ""}
        
        TEKST: {clean_text}
        """
        
        try:
            print("Fallback: Genererer en strikt kondensering.")
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                temperature=0.0,  # Meget præcis kondensering
                messages=[
                    {"role": "system", "content": "Du er ekspert i at sammenfatte dansk tekst kort og naturligt."},
                    {"role": "user", "content": strict_prompt}
                ],
            )
            result = response.choices[0].message.content.strip()
            print(f"Fallback-output: {result}")
            
            # Tilføj fortsættelsesstreger igen hvis nødvendigt
            if is_continuation and not result.startswith("- "):
                result = "- " + result
            if continues and not result.endswith(" -"):
                result = result + " -"
                
            return result
        except Exception as e:
            print(f"Fallback-fejl: {e}")
            return text  # Returnerer originalteksten som sidste udvej

    def condense_text(self, text: str, progress_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """Kondenserer tekst til kortere version med bevidsthed om fortsættelsesstreger."""
        try:
            print(f"Kondenserer tekst: {text}")
            if progress_callback:
                progress_callback("Starter kondensering med GPT")
                
            # Detect continuation markers
            is_continuation = text.startswith("- ")
            continues = text.endswith(" -")

            # Forsøg med flere temperatures
            proposals = []
            for temp in self.config.temperatures:
                condensed = self.get_condensation(text, temp, is_continuation, continues)
                if condensed and TextValidator.is_valid(condensed):
                    proposals.append(condensed)

            if proposals:
                # Vælg det længste gyldige forslag
                best_proposal = max(proposals, key=len)
                print(f"Valgte det længste gyldige forslag: {best_proposal}")
                return best_proposal

            # Ingen gyldige forslag: anvend fallback
            print("Ingen gyldige forslag fundet. Bruger fallback.")
            fallback_result = self.strict_fallback(text, is_continuation, continues)
            if fallback_result and TextValidator.is_valid(fallback_result):
                return fallback_result

            return None
        except Exception as e:
            print(f"Kondenseringsfejl: {e}")
            return None
            
    @staticmethod
    def ensure_continuation_consistency(prev_text: str, current_text: str) -> Tuple[str, str]:
        """
        Sikrer at fortsættelsesstreger er konsistente mellem to undertekster.
        
        Args:
            prev_text: Forrige undertekst
            current_text: Nuværende undertekst
            
        Returns:
            tuple: (justeret prev_text, justeret current_text)
        """
        # Hvis der ikke er forrige tekst eller en af teksterne er None, er der intet at justere
        if not prev_text or not current_text:
            return prev_text, current_text
        
        # Tjek om forrige tekst ender med fortsættelsesstreg, men nuværende ikke starter med fortsættelsesstreg
        if prev_text.endswith(" -") and not current_text.startswith("- "):
            # Hvis nuværende tekst starter med stort bogstav, skal forrige teksts fortsættelsesstreg erstattes med punktum
            if current_text and current_text[0].isupper():
                prev_text = prev_text[:-2] + "."
                print(f"Rettet fortsættelsesstreg til punktum: {prev_text}")
            else:
                # Nuværende tekst starter ikke med stort, men mangler fortsættelsesstreg
                current_text = "- " + current_text
                print(f"Tilføjet fortsættelsesstreg til start: {current_text}")
        
        # Tjek om forrige tekst ikke ender med fortsættelsesstreg, men nuværende starter med fortsættelsesstreg
        elif not prev_text.endswith(" -") and current_text.startswith("- "):
            # Tilføj fortsættelsesstreg til forrige tekst, hvis den ikke allerede har punktum
            if not prev_text.endswith(".") and not prev_text.endswith("?") and not prev_text.endswith("!"):
                prev_text = prev_text + " -"
                print(f"Tilføjet fortsættelsesstreg til slut: {prev_text}")
        
        return prev_text, current_text
    
    def condense_text_batch(self, texts: list, progress_callback: Optional[Callable[[str, int, int], None]] = None) -> list:
        """
        Kondenserer en batch af tekster med kontekstbevidsthed.
        
        Args:
            texts: Liste af tekster der skal kondenseres
            progress_callback: Callback funktion for fremskridt (tekst, current_index, total)
            
        Returns:
            list: Liste af kondenserede tekster
        """
        result = []
        total = len(texts)
        
        for i, text in enumerate(texts):
            # Rapportér fremskridt
            if progress_callback:
                progress_callback(f"Kondenserer tekst {i+1}/{total}", i, total)
            
            # Kondenser den aktuelle tekst
            condensed = self.condense_text(text)
            if not condensed:
                condensed = text  # Fallback til originaltekst hvis kondensering fejler
            
            # Justér fortsættelsesstreger hvis der er forrige tekst
            if i > 0:
                prev_text = result[-1]
                prev_text, condensed = self.ensure_continuation_consistency(prev_text, condensed)
                result[-1] = prev_text
            
            result.append(condensed)
            
        return result

def condense_texts(texts: list, max_chars: int, progress_callback: Optional[Callable[[str, int, int], None]] = None) -> list:
    """
    Wrapper-funktion for kondensering af en batch tekster med kontekstbevidsthed.
    
    Args:
        texts: Liste af tekster der skal kondenseres
        max_chars: Maksimalt antal tegn i output for hver tekst
        progress_callback: Callback funktion for fremskridt
        
    Returns:
        list: Liste af kondenserede tekster
    """
    config = CondensationConfig(max_chars=max_chars)
    condenser = TextCondenser(config)
    return condenser.condense_text_batch(texts, progress_callback)