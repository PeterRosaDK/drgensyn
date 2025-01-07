import os
from typing import Optional, Callable, List
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
    """Håndterer kondensering af tekst via GPT."""
    
    SYSTEM_PROMPT = """Du er ekspert i at forkorte danske undertekster.
    Din opgave er at omskrive teksten til en kortere version der bevarer den væsentlige mening.
    Du skal:
    1. Bevare den oprindelige mening så meget som muligt
    2. Bruge naturligt dansk sprog
    3. Beholde så mange af de oprindelige ord som muligt
    4. Aldrig bruge forkortelser eller specialtegn
    5. Returnere teksten som én enkelt linje (uden linjeskift)"""

    def __init__(self, config):
        self.config = config
        load_dotenv()
        self.client = AzureOpenAI(
            azure_endpoint=os.getenv("OPENAI_AZURE_ENDPOINT"),
            api_key=os.getenv("OPENAI_AZURE_API_KEY"),
            api_version="2023-06-01-preview"
        )

    def get_condensation(self, text: str, temperature: float) -> Optional[str]:
        """Genererer ét kondenseringsforslag fra GPT."""
        try:
            target_length = self.config.max_chars
            print(f"Genererer forslag (temp={temperature})")
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": f"""
                        Omskriv denne tekst til en kortere version på omkring {target_length} tegn.
                        Den må IKKE være længere end {target_length} tegn.
                        Bevar den væsentlige mening og brug naturligt dansk sprog.
                        
                        TEKST: {text}
                        """}
                ],
            )
            output = response.choices[0].message.content.strip()
            print(f"GPT output: {output}")
            return output if len(output) <= target_length else None
        except Exception as e:
            print(f"GPT fejl: {e}")
            return None

    def strict_fallback(self, text: str) -> str:
        """Fallback der sikrer kondensering uden '...' og med afrundet mening."""
        strict_prompt = """
        Din opgave er at forkorte følgende tekst til én kort sætning, der bevarer den vigtigste mening. 
        Hvis der er flere sætninger, skal du kun inkludere den mest betydningsfulde.
        Teksten skal være naturligt afrundet og må ALDRIG slutte med "..." eller føles ufuldstændig.
        
        TEKST: {text}
        """
        try:
            print("Fallback: Genererer en strikt kondensering.")
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                temperature=0.0,  # Meget præcis kondensering
                messages=[
                    {"role": "system", "content": "Du er ekspert i at sammenfatte dansk tekst kort og naturligt."},
                    {"role": "user", "content": strict_prompt.format(text=text)}
                ],
            )
            result = response.choices[0].message.content.strip()
            print(f"Fallback-output: {result}")
            return result
        except Exception as e:
            print(f"Fallback-fejl: {e}")
            return text  # Returnerer originalteksten som sidste udvej

    def condense_text(self, text: str, progress_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
        """Kondenserer tekst til kortere version."""
        try:
            print(f"Kondenserer tekst: {text}")
            if progress_callback:
                progress_callback("Starter kondensering med GPT")

            # Forsøg med flere temperatures
            proposals = []
            for temp in self.config.temperatures:
                condensed = self.get_condensation(text, temp)
                if condensed and TextValidator.is_valid(condensed):
                    proposals.append(condensed)

            if proposals:
                # Vælg det længste gyldige forslag
                best_proposal = max(proposals, key=len)
                print(f"Valgte det længste gyldige forslag: {best_proposal}")
                return best_proposal

            # Ingen gyldige forslag: anvend fallback
            print("Ingen gyldige forslag fundet. Bruger fallback.")
            fallback_result = self.strict_fallback(text)
            if fallback_result and TextValidator.is_valid(fallback_result):
                return fallback_result

            return None
        except Exception as e:
            print(f"Kondenseringsfejl: {e}")
            return None

def condense_text(text: str, max_chars: int, progress_callback: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Wrapper-funktion for kondensering"""
    config = CondensationConfig(max_chars=max_chars)
    condenser = TextCondenser(config)
    return condenser.condense_text(text, progress_callback)
