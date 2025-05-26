import sys
from dotenv import load_dotenv
import os
import configparser
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QFileDialog, QLabel, QCheckBox, QSpinBox, 
    QProgressBar, QFrame, QDoubleSpinBox, QGroupBox, QComboBox, QTextEdit, QMenu
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QPalette, QColor, QBrush
from typing import List, Tuple, Optional
import json
import pysrt
from DrGenkend import recognize_speech
from DrSegment import segment_json
from DrKondens import condense_texts

class Colors:
    """Farvetema for applikationen"""
    BG_MAIN = "#E8F5E9"        # Lysegrøn baggrund
    ## BG_SECTION = "#F1F8E9"     # Lysere grøn til sektioner
    BG_SECTION = "#DDE8D4"     # Lysere grøn til sektioner
    FG_HEADER = "#2E7D32"      # Mørkegrøn til overskrifter
    FG_TEXT = "#212121"        # Næsten sort til tekst
    BUTTON = "#4CAF50"         # Grøn til knapper
    BUTTON_TEXT = "#FFFFFF"    # Hvid tekst på knapper
    PROGRESS = "#81C784"       # Mellemgrøn til progressbar

from typing import List, Tuple, Optional

class TextFormatter:
    """Håndterer formatering af undertekster"""
    def __init__(self, max_chars_per_line: int = 37):
        self.max_chars = max_chars_per_line
        
        # Udvidet liste af småord at dele ved
        self.small_words = [
            'og', 'at', 'i', 'på', 'med', 'til', 'fra', 'om', 'så', 'der', 
            'den', 'det', 'de', 'han', 'hun', 'vi', 'jeg', 'du', 'men',
            'eller', 'hvis', 'når', 'som', 'hvor', 'hvad', 'hvilket', 'hvilken'
        ]
        
        # Tegnsætning at dele ved
        self.sentence_endings = ['. ', '! ', '? ']
        self.other_punctuation = [': ', '; ', ', ', ' - ', ' – ']

    def try_punctuation_split(self, text: str) -> Optional[Tuple[str, str]]:
        """Forsøger at dele tekst ved tegnsætning"""
        # Prøv først sætningsafslutninger
        for punct in self.sentence_endings + self.other_punctuation:
            if punct in text:
                # Find sidste tegn der giver en første linje inden for max_chars
                pos = text.rfind(punct, 0, self.max_chars)
                if pos > 0:  # Hvis vi fandt tegnet
                    # Inkluder tegnet men ikke mellemrummet efter
                    end_pos = pos + len(punct.rstrip())
                    line1 = text[:end_pos]
                    line2 = text[end_pos:].strip()
                    
                    # Tjek om begge linjer overholder max_chars
                    if len(line1) <= self.max_chars and len(line2) <= self.max_chars:
                        print(f"Fandt deling ved '{punct}': \nLinje 1: {line1}\nLinje 2: {line2}")
                        return line1, line2
                    else:
                        print(f"Deling ved '{punct}' gav for lange linjer ({len(line1)}, {len(line2)})")
        
        return None

    def try_word_split(self, text: str) -> Optional[Tuple[str, str]]:
        """Forsøger at dele tekst ved småord"""
        for word in self.small_words:
            pattern = f" {word} "
            pos = text.lower().rfind(pattern, 0, self.max_chars)
            if pos > 0:
                line1 = text[:pos].strip()
                line2 = text[pos + 1:].strip()  # +1 for at fjerne mellemrum
                
                if len(line1) <= self.max_chars and len(line2) <= self.max_chars:
                    print(f"Fandt deling ved '{word}': \nLinje 1: {line1}\nLinje 2: {line2}")
                    return line1, line2
        return None

    def format_text(self, text: str) -> Tuple[str, bool]:
        """
        Formaterer tekst efter reglerne. Returnerer (formateret_tekst, needs_condensing)
        """
        text = text.strip()
        print(f"\nFormaterer tekst ({len(text)} tegn): {text}")

        # Regel 1: Hvis teksten kan være på én linje
        if len(text) <= self.max_chars:
            print("Tekst er kort nok til én linje")
            return text, False

        # Regel 2: Prøv at dele ved tegnsætning
        result = self.try_punctuation_split(text)
        if result:
            line1, line2 = result
            print("Bruger deling ved tegnsætning")
            return f"{line1}\n{line2}", False

        # Regel 3: Prøv at dele ved småord
        result = self.try_word_split(text)
        if result:
            line1, line2 = result
            print("Bruger deling ved småord")
            return f"{line1}\n{line2}", False
            
        # Regel 4: Prøv at dele ved sidste ord der passer
        words = text.split()
        line1 = []
        current_length = 0

        for word in words:
            new_length = current_length + len(word) + (1 if line1 else 0)
            if new_length <= self.max_chars:
                if line1:
                    current_length += 1  # mellemrum
                line1.append(word)
                current_length += len(word)
            else:
                break

        if line1 and len(words) > len(line1):
            line1_text = ' '.join(line1)
            line2_text = ' '.join(words[len(line1):])
            
            if len(line1_text) <= self.max_chars and len(line2_text) <= self.max_chars:
                print(f"Bruger ordbaseret deling:\nLinje 1: {line1_text}\nLinje 2: {line2_text}")
                return f"{line1_text}\n{line2_text}", False

        # Regel 5: Hvis ingen delinger virkede OG teksten er over 2 × max_chars
        if len(text) > 2 * self.max_chars:
            print(f"Tekst for lang til todeling ({len(text)} > {2 * self.max_chars})")
            return text, True

        print(f"Kunne ikke finde god deling. Længde: {len(text)}")
        return text, True

def adjust_subtitle_gaps(subs, fps=25):
    """
    Justerer tidsforskellen mellem undertekster ved at forlænge sluttiden for den første tekst,
    hvis afstanden mellem slutningen af én tekst og starten af den næste er:
    - Over 4 frames og inden for 25 frames (1 sekund).
    """
    min_gap = 4 / fps  # 4 frames (sekunder)
    max_gap = 1.0      # 1 sekund (25 frames ved fps=25)

    for i in range(len(subs) - 1):
        current_sub = subs[i]
        next_sub = subs[i + 1]

        # Konverter tider til sekunder
        end_time = current_sub.end.ordinal / 1000.0  # Sluttidspunkt i sekunder
        start_time = next_sub.start.ordinal / 1000.0  # Starttidspunkt i sekunder

        gap = start_time - end_time

        # Hvis tidsforskellen er mellem 4 frames og 1 sekund (25 frames)
        if min_gap < gap <= max_gap:
            # Forlæng sluttiden på den første tekst til præcis 4 frames før næste tekst
            adjusted_end_time = start_time - min_gap
            current_sub.end = pysrt.SubRipTime(seconds=adjusted_end_time)

class ProcessingThread(QThread):
    """Håndterer asynkron processering"""
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, input_file: str, modules: dict, config: dict):
        super().__init__()
        self.input_file = input_file
        self.modules = modules
        self.config = config

        # Indlæs API-nøglen fra .env hvis genkend er aktiveret
        if self.modules.get("genkend"):
            load_dotenv()
            api_key = os.getenv("SPEECHMATICS_API_KEY")
            if not api_key:
                raise ValueError("FEJL: Ingen API-nøgle fundet i .env-filen. Tilføj 'SPEECHMATICS_API_KEY=<din_nøgle>'.")
            self.config["api_key"] = api_key

        # Fjern eventuelle tomme værdier fra additional_vocab
        if "additional_vocab" in self.config:
            self.config["additional_vocab"] = [
                word for word in self.config["additional_vocab"] 
                if word.get("content", "").strip()
            ]

    def run(self):
        try:
            base_path = os.path.splitext(self.input_file)[0]
            current_progress = 0
            input_for_next = self.input_file  # Start med input filen
            
            # Dr. Genkend
            if self.modules.get("genkend"):
                self.status_update.emit("Starter talegenkendelse...")
                self.progress_update.emit(current_progress)

                def genkend_callback(msg):
                    self.status_update.emit(msg)
                    if "Job created with ID:" in msg:
                        self.progress_update.emit(10)
                    elif "Converting" in msg:
                        self.progress_update.emit(5)
                    elif "Transcription received" in msg:
                        self.progress_update.emit(30)

                json_path = f"{base_path}_transcript.json"
                result = recognize_speech(
                    input_file=input_for_next,
                    config=self.config,
                    progress_callback=genkend_callback
                )

                if not result:
                    raise Exception("Fejl i talegenkendelse")
                    
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                
                current_progress = 33
                self.progress_update.emit(current_progress)
                input_for_next = json_path
            
            # Dr. Segment
            if self.modules.get("segment"):
                self.status_update.emit("Starter segmentering...")
                self.progress_update.emit(current_progress)

                def segment_callback(msg):
                    self.status_update.emit(msg)

                # Load JSON fra tidligere step eller input fil
                with open(input_for_next, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                
                # Tilføj merge_threshold_sec til config hvis ikke allerede sat
                segment_config = {"merge_threshold_sec": self.config.get("merge_threshold_sec", 7.0)}
                
                srt_path = f"{base_path}.srt"
                srt_items = segment_json(
                    json_data=json_data,
                    config=segment_config,
                    progress_callback=segment_callback
                )

                if not srt_items:
                    raise Exception("Fejl i segmentering")
                
                subs = pysrt.SubRipFile(srt_items)
                subs.save(srt_path, encoding='utf-8')
                
                current_progress = 66
                self.progress_update.emit(current_progress)
                input_for_next = srt_path
            
            # Dr. Kondens
            if self.modules.get("kondens"):
                self.status_update.emit("Starter tekstformatering og kondensering...")
                self.progress_update.emit(current_progress)

                # Load SRT fra tidligere step eller input fil
                subs = pysrt.open(input_for_next)
                max_chars = self.config.get("max_chars", 37)

                formatter = TextFormatter(max_chars)
                stats = {"formatted": 0, "condensed": 0, "unchanged": 0, "errors": 0}

                total_subs = len(subs)
                self.status_update.emit(f"Behandler {total_subs} undertekster...")

                texts_to_condense = []
                text_indices = []
                formatted_texts = []
                
                for i, sub in enumerate(subs):
                    sub_progress = int(current_progress + (15 * (i / total_subs)))
                    self.progress_update.emit(sub_progress)

                    # Brug TextFormatter til at tjekke om teksten skal kondenseres
                    formatted_text, needs_condensing = formatter.format_text(sub.text)
                    formatted_texts.append(formatted_text)
                    
                    if needs_condensing:
                        texts_to_condense.append(sub.text)
                        text_indices.append(i)
                    else:
                        sub.text = formatted_text
                        if '\n' in formatted_text:
                            stats["formatted"] += 1
                        else:
                            stats["unchanged"] += 1
                
                # Kondenser alle tekster der behøver det på én gang
                if texts_to_condense:
                    self.status_update.emit(f"Kondenserer {len(texts_to_condense)} tekster...")
                    
                    # Brug den nye condense_texts funktion for batch processing
                    condensed_texts = condense_texts(
                        texts=texts_to_condense,
                        max_chars=max_chars,
                        progress_callback=lambda msg, i, total: self.status_update.emit(f"{msg} ({i+1}/{total})")
                    )
                    
                    # Opdater undertekster med kondenserede versioner
                    for idx, condensed in zip(text_indices, condensed_texts):
                        if condensed:
                            # Formatér den kondenserede tekst
                            formatted_condensed, still_needs_condensing = formatter.format_text(condensed)
                            if not still_needs_condensing:
                                subs[idx].text = formatted_condensed
                            else:
                                subs[idx].text = condensed
                            stats["condensed"] += 1
                        else:
                            # Hvis kondensering fejlede, behold original formatering
                            subs[idx].text = formatted_texts[idx]
                            stats["errors"] += 1
                    
                    sub_progress = int(current_progress + 25)
                    self.progress_update.emit(sub_progress)

                # Justér undertekstgaps før gemning
                adjust_subtitle_gaps(subs, fps=25)

                # Gem opdateret SRT
                output_path = f"{base_path}_final.srt"
                subs.save(output_path, encoding='utf-8')

                # Vis statistik
                self.status_update.emit(
                    f"Behandling færdig:\n"
                    f"{stats['unchanged']} tekster var korte nok\n"
                    f"{stats['formatted']} tekster blev formateret på to linjer\n"
                    f"{stats['condensed']} tekster blev forkortet\n"
                    f"{stats['errors']} tekster fejlede i kondensering"
                )

            current_progress = 100
            self.progress_update.emit(current_progress)
            self.status_update.emit("Behandling gennemført!")
            self.finished.emit(True, "Færdig!")
        
        except Exception as e:
            self.finished.emit(False, f"Fejl: {str(e)}")

class DropZone(QLabel):
    fileDropped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setText("Træk video/lydfil hertil")
        self.setAlignment(Qt.AlignCenter)
        self.current_file = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if files:
            self.fileDropped.emit(files[0])

    def setFile(self, file_path):
        self.current_file = file_path
        self.setText(f"Valgt fil: {os.path.basename(file_path)}\n(Slip ny fil her for at ændre)")

class QueueItem(QtWidgets.QListWidgetItem):
    """Repræsenterer en fil i køen med status"""
    STATUS_COLORS = {
        'pending': QColor('#666666'),  # Grå
        'processing': QColor('#2196F3'),  # Blå
        'completed': QColor('#4CAF50'),  # Grøn
        'error': QColor('#F44336')  # Rød
    }
    
    def __init__(self, file_path: str, modules: list):
        super().__init__()
        self.file_path = file_path
        self.modules = modules
        self.status = 'pending'
        self.update_display()
        
    def update_status(self, new_status: str):
        """Opdaterer status og visning"""
        self.status = new_status
        self.update_display()
        
    def update_display(self):
        """Opdaterer hvordan køelementet vises"""
        module_names = {
            'genkend': 'Talegenkendelse',
            'segment': 'Segmentering',
            'kondens': 'Kondensering'
        }
        active_modules = [module_names[m] for m in self.modules if m in module_names]
        
        status_symbols = {
            'pending': '⌛',
            'processing': '⚙️',
            'completed': '✅',
            'error': '❌'
        }
        
        # Vis filnavn, aktive moduler og status
        display_text = f"{status_symbols[self.status]} {os.path.basename(self.file_path)}\n"
        display_text += f"   Moduler: {' → '.join(active_modules)}"
        
        self.setText(display_text)
        self.setForeground(QBrush(self.STATUS_COLORS[self.status]))

class DrOrkestrator(QMainWindow):
    def __init__(self):
        super().__init__()
        self.custom_dictionary = []
        self.init_ui()
        load_settings_to_gui(self) # Indlæs indstillinger fra config.ini
        self.current_file = None  # Holder styr på den aktuelt valgte fil

        # Forbind checkbox ændringer med UI-opdateringer
        self.genkend_check.stateChanged.connect(self.on_module_change)
        self.segment_check.stateChanged.connect(self.on_module_change)
        self.kondens_check.stateChanged.connect(self.on_module_change)

        # Tilføj kontekstmenu til kø-listen
        self.queue_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_list.customContextMenuRequested.connect(self.show_queue_context_menu)

    def show_queue_context_menu(self, position):
        """Viser kontekstmenu for kø-elementer"""
        menu = QMenu()
        remove_action = menu.addAction("Fjern fra kø")
        
        # Kun vis menu hvis der er valgt et element
        if self.queue_list.currentItem():
            action = menu.exec_(self.queue_list.mapToGlobal(position))
            if action == remove_action:
                self.remove_selected_item()

    def remove_selected_item(self):
        """Fjerner det valgte element fra køen"""
        current_row = self.queue_list.currentRow()
        if current_row >= 0:
            self.queue_list.takeItem(current_row)

    def on_module_change(self):
        self.update_dropzone_text()
        if not self.validate_modules():
            self.start_button.setEnabled(False)
        else:
            self.start_button.setEnabled(True)

    def init_ui(self):
        self.setWindowTitle("Dr. Gensyn")
        self.setMinimumSize(800, 600)
        
        # Sæt hovedbaggrund og skygge
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {Colors.BG_MAIN};
            }}
            
            QMainWindow > QWidget {{
                border: 1px solid #cccccc;
                border-radius: 10px;
                background-color: {Colors.BG_MAIN};
            }}
            
            QWidget#mainWidget {{
                border: none;
            }}
        """)
        
        # Tilføj skyggeeffekt
        shadow = self.style().standardPalette().color(QPalette.Shadow)
        self.setGraphicsEffect(QtWidgets.QGraphicsDropShadowEffect(
            blurRadius=20,
            color=shadow,
            offset=QtCore.QPointF(0, 0)
        ))

        # Hovedwidget og layout
        main_widget = QWidget()
        main_widget.setObjectName("mainWidget")  # For at undgå border på hovedwidget
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # Input sektion
        input_group = self.create_input_section()
        layout.addWidget(input_group)

        # Modul sektion
        module_group = self.create_module_section()
        layout.addWidget(module_group)

        # Kø sektion
        queue_group = QGroupBox("Kø")
        queue_group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Colors.BG_SECTION};
                border-radius: 5px;
                font-weight: bold;
                padding: 10px;
            }}
        """)
        queue_layout = QVBoxLayout()
        self.queue_list = QtWidgets.QListWidget()  # Opret kø-widget
        self.queue_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid #999;
                background-color: white;
                border-radius: 5px;
                padding: 5px;
            }}
            QListWidget::item {{
                padding: 5px;
            }}
            QListWidget::item:selected {{
                background-color: {Colors.BUTTON};
                color: {Colors.BUTTON_TEXT};
            }}
        """)
        queue_layout.addWidget(self.queue_list)
        queue_group.setLayout(queue_layout)
        layout.addWidget(queue_group)

        # Status sektion
        status_group = self.create_status_section()
        layout.addWidget(status_group)

        # Start knap
        self.start_button = QPushButton("Start")
        self.start_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BUTTON};
                color: {Colors.BUTTON_TEXT};
                border: none;
                padding: 10px;
                font-size: 16px;
                font-weight: bold;
                border-radius: 5px;
            }}
            QPushButton:hover {{
                background-color: #45a049;
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
            }}
        """)
        self.start_button.clicked.connect(self.start_processing)
        layout.addWidget(self.start_button)


    def get_file_filter(self):
        if self.genkend_check.isChecked():
            return "Video/Lyd filer (*.mp4 *.wav *.mpg)"
        elif self.segment_check.isChecked():
            return "JSON filer (*.json)"
        elif self.kondens_check.isChecked():
            return "SRT filer (*.srt)"
        return "Alle filer (*.*)"

    def validate_modules(self):
        """Tjekker om kombinationen af moduler giver mening"""
        if not any([self.genkend_check.isChecked(), self.segment_check.isChecked(), self.kondens_check.isChecked()]):
            self.update_status("Vælg mindst ét modul")
            return False

        # Tjek for ugyldige kombinationer
        if self.kondens_check.isChecked() and self.genkend_check.isChecked() and not self.segment_check.isChecked():
            self.update_status("Dr. Kondens kræver SRT-fil. Aktivér venligst Dr. Segment også.")
            return False

        # Tjek kun filtype hvis en fil er valgt
        if hasattr(self, 'current_file') and self.current_file is not None:
            ext = os.path.splitext(self.current_file)[1].lower()
            if self.genkend_check.isChecked() and ext not in ['.mp4', '.wav', '.mpg']:
                self.update_status("Dr. Genkend kræver video- eller lydfil")
                return False
            elif self.segment_check.isChecked() and not self.genkend_check.isChecked() and ext != '.json':
                self.update_status("Dr. Segment kræver JSON-fil når Dr. Genkend ikke er aktiv")
                return False
            elif self.kondens_check.isChecked() and not self.segment_check.isChecked() and ext != '.srt':
                self.update_status("Dr. Kondens kræver SRT-fil når Dr. Segment ikke er aktiv")
                return False

        return True

    def validate_file_for_modules(self, file_path: str, modules: list) -> bool:
        """Validerer at filtypen matcher de valgte moduler"""
        ext = os.path.splitext(file_path)[1].lower()
        
        if 'genkend' in modules and ext not in ['.mp4', '.wav', '.mpg']:
            self.update_status("Dr. Genkend kræver video- eller lydfil")
            return False
        elif 'segment' in modules and not 'genkend' in modules and ext != '.json':
            self.update_status("Dr. Segment kræver JSON-fil når Dr. Genkend ikke er aktiv")
            return False
        elif 'kondens' in modules and not 'segment' in modules and ext != '.srt':
            self.update_status("Dr. Kondens kræver SRT-fil når Dr. Segment ikke er aktiv")
            return False
            
        return True

    def update_dropzone_text(self):
        if self.genkend_check.isChecked():
            self.drop_zone.setText("Træk video/lydfil hertil")
        elif self.segment_check.isChecked():
            self.drop_zone.setText("Træk JSON-fil hertil")
        elif self.kondens_check.isChecked():
            self.drop_zone.setText("Træk SRT-fil hertil")

    def create_input_section(self) -> QGroupBox:
        group = QGroupBox("Input")
        group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Colors.BG_SECTION};
                border-radius: 5px;
                font-weight: bold;
                padding: 10px;
            }}
        """)
        layout = QVBoxLayout()

        # Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.fileDropped.connect(self.handle_dropped_file)
        self.drop_zone.setMinimumHeight(100)
        self.drop_zone.setStyleSheet(f"""
            QLabel {{
                border: 2px dashed #999;
                border-radius: 5px;
                background-color: white;
                padding: 20px;
                color: #666;
            }}
            QLabel:hover {{
                border-color: {Colors.BUTTON};
                color: {Colors.BUTTON};
            }}
        """)
        layout.addWidget(self.drop_zone)

        # Fil vælger knap
        browse_button = QPushButton("Eller vælg fil med stifinder")
        browse_button.setStyleSheet(f"""
            background-color: {Colors.BUTTON};
            color: {Colors.BUTTON_TEXT};
            border: none;
            padding: 5px 10px;
            border-radius: 3px;
        """)
        browse_button.clicked.connect(self.browse_file)
        layout.addWidget(browse_button)

        group.setLayout(layout)
        return group

    def browse_file(self):
        file_filter = self.get_file_filter()
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Vælg fil",
            "",
            file_filter
        )
        if file_name:
            self.handle_dropped_file(file_name)


    def handle_dropped_file(self, file_path):
        # Find aktive moduler
        active_modules = []
        if self.genkend_check.isChecked():
            active_modules.append('genkend')
        if self.segment_check.isChecked():
            active_modules.append('segment')
        if self.kondens_check.isChecked():
            active_modules.append('kondens')
            
        if not active_modules:
            self.update_status("Vælg mindst ét modul")
            return
            
        # Validér filtype mod valgte moduler
        if not self.validate_file_for_modules(file_path, active_modules):
            return
            
        # Opret nyt kø-element
        queue_item = QueueItem(file_path, active_modules)
        self.queue_list.addItem(queue_item)
        self.update_status(f"Tilføjet til kø: {os.path.basename(file_path)}")
        
    def process_next_file(self):
        if self.queue_list.count() == 0:
            self.update_status("Køen er tom")
            return

        # Find første ikke-færdige fil i køen
        for i in range(self.queue_list.count()):
            current_item = self.queue_list.item(i)
            if current_item.status == 'pending':
                # Flyt den til toppen af køen
                item = self.queue_list.takeItem(i)
                self.queue_list.insertItem(0, item)
                
                # Start behandling
                current_item.update_status('processing')
                
                config = self.get_config()
                self.processing_thread = ProcessingThread(
                    input_file=current_item.file_path,
                    modules={module: True for module in current_item.modules},
                    config=config
                )
                
                self.processing_thread.status_update.connect(self.update_status)
                self.processing_thread.progress_update.connect(self.update_progress)
                self.processing_thread.finished.connect(self.processing_finished)
                self.processing_thread.start()
                return
                
        # Hvis vi når hertil, er der ingen pending filer
        self.start_button.setEnabled(True)
        self.update_status("Alle filer er færdigbehandlet")

    def processing_finished(self, success: bool, msg: str):
        if self.queue_list.count() == 0:
            return
            
        current_item = self.queue_list.item(0)
        if success:
            current_item.update_status('completed')
            # Flyt elementet til bunden af køen
            item = self.queue_list.takeItem(0)
            self.queue_list.addItem(item)
            
            self.update_status(f"Behandling gennemført: {msg}")
            self.progress_bar.setValue(100)
            
            # Start næste fil hvis der er flere
            if self.queue_list.count() > 0 and self.queue_list.item(0).status == 'pending':
                self.process_next_file()
            else:
                self.start_button.setEnabled(True)
                self.update_status("Alle filer er færdigbehandlet")
        else:
            current_item.update_status('error')
            self.update_status(f"Fejl under behandling: {msg}")
            self.start_button.setEnabled(True)
            self.progress_bar.setValue(0)

    def create_module_section(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setSpacing(10)

        # Venstre: Moduler gruppe
        modules_group = QGroupBox("Vælg de ønskede funktioner")
        modules_group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Colors.BG_SECTION};
                border-radius: 5px;
                font-weight: bold;
                margin-top: 1.5ex;
                padding: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
            }}
        """)

        modules_layout = QVBoxLayout()
        modules_layout.setContentsMargins(5, 5, 5, 5)

        # Dr. Genkend – hovedvalg
        genkend_layout = QHBoxLayout()
        self.genkend_check = QCheckBox("Talegenkendelse")
        self.genkend_check.setChecked(True)
        self.language_combo = QComboBox()
        self.language_combo.addItems(["Dansk", "Engelsk", "Auto"])
        genkend_layout.addWidget(self.genkend_check)
        genkend_layout.addWidget(self.language_combo)
        genkend_layout.addStretch()
        modules_layout.addLayout(genkend_layout)

        # Underindstillinger til Dr. Genkend
        genkend_settings_layout = QHBoxLayout()

        self.speaker_sens_spin = QDoubleSpinBox()
        self.speaker_sens_spin.setRange(0.0, 1.0)
        self.speaker_sens_spin.setSingleStep(0.1)
        self.speaker_sens_spin.setValue(0.8)
        self.speaker_sens_spin.setPrefix("Speaker: ")

        self.punct_sens_spin = QDoubleSpinBox()
        self.punct_sens_spin.setRange(0.0, 1.0)
        self.punct_sens_spin.setSingleStep(0.1)
        self.punct_sens_spin.setValue(0.4)
        self.punct_sens_spin.setPrefix("Tegn: ")

        self.volume_thresh_spin = QDoubleSpinBox()
        self.volume_thresh_spin.setRange(0.0, 10.0)
        self.volume_thresh_spin.setSingleStep(0.1)
        self.volume_thresh_spin.setValue(2.4)
        self.volume_thresh_spin.setPrefix("Vol: ")

        genkend_settings_layout.addWidget(self.speaker_sens_spin)
        genkend_settings_layout.addWidget(self.punct_sens_spin)
        genkend_settings_layout.addWidget(self.volume_thresh_spin)
        genkend_settings_layout.addStretch()
        modules_layout.addLayout(genkend_settings_layout)

        # Dr. Segment
        segment_layout = QHBoxLayout()
        self.segment_check = QCheckBox("Segmentering")
        self.segment_check.setChecked(True)
        self.merge_threshold_spin = QSpinBox()
        self.merge_threshold_spin.setRange(1, 10)
        self.merge_threshold_spin.setValue(6)
        self.merge_threshold_spin.setPrefix("Ideal: ")
        self.merge_threshold_spin.setSuffix(" sekunder")
        segment_layout.addWidget(self.segment_check)
        segment_layout.addWidget(self.merge_threshold_spin)
        segment_layout.addStretch()
        modules_layout.addLayout(segment_layout)

        # Dr. Kondens
        kondens_layout = QHBoxLayout()
        self.kondens_check = QCheckBox("Kondensering")
        self.kondens_check.setChecked(True)
        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(20, 150)
        self.max_chars_spin.setValue(37)
        self.max_chars_spin.setPrefix("Linjelængde: ")
        kondens_layout.addWidget(self.kondens_check)
        kondens_layout.addWidget(self.max_chars_spin)
        kondens_layout.addStretch()
        modules_layout.addLayout(kondens_layout)

        modules_group.setLayout(modules_layout)
        layout.addWidget(modules_group, stretch=1)

        # Højre: Ordbog gruppe
        dictionary_group = QGroupBox("Ordbog")
        dictionary_group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Colors.BG_SECTION};
                border-radius: 5px;
                font-weight: bold;
                margin-top: 1.5ex;
                padding: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
            }}
        """)
        dictionary_layout = QVBoxLayout()
        dictionary_layout.setContentsMargins(5, 5, 5, 5)

        self.dictionary_input = QTextEdit(self)
        self.dictionary_input.setPlaceholderText(
            "Skriv ét ord eller en sætning per linje.\n"
            "CEO\nfinanskrise\ngnocchi: nåki"
        )
        self.dictionary_input.setMaximumHeight(100)
        self.dictionary_input.textChanged.connect(self.update_custom_dictionary)
        dictionary_layout.addWidget(self.dictionary_input)

        dictionary_group.setLayout(dictionary_layout)
        layout.addWidget(dictionary_group, stretch=2)

        return container

    def create_status_section(self) -> QGroupBox:
        group = QGroupBox("Status")
        group.setStyleSheet(f"""
            QGroupBox {{
                background-color: {Colors.BG_SECTION};
                border-radius: 5px;
                font-weight: bold;
                padding: 10px;
            }}
        """)
        layout = QVBoxLayout()

        # Status label
        self.status_label = QLabel("Klar")
        self.status_label.setStyleSheet(f"""
            color: {Colors.FG_TEXT};
            font-size: 14px;
            padding: 5px;
        """)
        layout.addWidget(self.status_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: none;
                border-radius: 5px;
                text-align: center;
                background-color: white;
            }}
            QProgressBar::chunk {{
                background-color: {Colors.PROGRESS};
                border-radius: 5px;
            }}
        """)
        layout.addWidget(self.progress_bar)

        group.setLayout(layout)
        return group

    def browse_file(self):
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Vælg fil",
            "",
            "Video/Lyd filer (*.mp4 *.wav *.mpg);;Alle filer (*.*)"
        )
        if file_name:
            self.handle_dropped_file(file_name)

    def update_status(self, message: str):
        self.status_label.setText(message)

    def update_progress(self, value: int):
        self.progress_bar.setValue(value)

    def start_processing(self):
        if self.queue_list.count() == 0:
            self.update_status("Køen er tom. Tilføj filer først.")
            return

        # Gem indstillinger til config.ini
        save_settings_from_gui(self)

        # Deaktiver start-knap og nulstil progress
        self.start_button.setEnabled(False)
        self.progress_bar.setValue(0)

        # Start processing af første fil i køen
        self.process_next_file()

    def get_config(self) -> dict:
        language_map = {
            "Dansk": "da",
            "Engelsk": "en",
            "Auto": "auto"
        }
        return {
            "language": language_map.get(self.language_combo.currentText(), "auto"),
            "additional_vocab": self.custom_dictionary,
            "merge_threshold_sec": self.merge_threshold_spin.value(),
            "speaker_sensitivity": self.speaker_sens_spin.value(),
            "punctuation_sensitivity": self.punct_sens_spin.value(),
            "volume_threshold": self.volume_thresh_spin.value()
        }

    def update_custom_dictionary(self):
        """Parser inputfeltet og opdaterer ordbogen."""
        text = self.dictionary_input.toPlainText().strip()
        self.custom_dictionary = []  # Nulstil ordbogen
        if text:
            lines = text.split("\n")
            for line in lines:
                if ':' in line:
                    # Parse ord med sounds_like
                    content, sounds_like = line.split(":", 1)
                    entry = {
                        "content": content.strip(),
                        "sounds_like": [s.strip() for s in sounds_like.split(",")]
                    }
                else:
                    # Bare et enkelt ord
                    entry = {"content": line.strip()}
                self.custom_dictionary.append(entry)
        self.update_status(f"Ordbog opdateret: {len(self.custom_dictionary)} ord")

# Indlæs og gem indstillinger
SETTINGS_FILE = "settings.ini"

def save_settings_from_gui(window):
    config = configparser.ConfigParser()
    config["GENKEND"] = {
        "language": window.language_combo.currentText(),
        "speaker_sensitivity": f"{window.speaker_sens_spin.value():.2f}",
        "punctuation_sensitivity": f"{window.punct_sens_spin.value():.2f}",
        "volume_threshold": f"{window.volume_thresh_spin.value():.2f}",
    }
    config["SEGMENT"] = {
        "merge_threshold_sec": str(window.merge_threshold_spin.value())
    }
    config["KONDENS"] = {
        "max_chars": str(window.max_chars_spin.value())
    }

    with open(SETTINGS_FILE, "w") as f:
        config.write(f)

def load_settings_to_gui(window):
    if not os.path.exists(SETTINGS_FILE):
        return  # Gør intet hvis filen ikke findes

    config = configparser.ConfigParser()
    config.read(SETTINGS_FILE)

    # GENKEND
    language = config.get("GENKEND", "language", fallback="Auto")
    index = window.language_combo.findText(language)
    if index >= 0:
        window.language_combo.setCurrentIndex(index)

    window.speaker_sens_spin.setValue(config.getfloat("GENKEND", "speaker_sensitivity", fallback=0.8))
    window.punct_sens_spin.setValue(config.getfloat("GENKEND", "punctuation_sensitivity", fallback=0.4))
    window.volume_thresh_spin.setValue(config.getfloat("GENKEND", "volume_threshold", fallback=2.4))

    # SEGMENT
    window.merge_threshold_spin.setValue(config.getint("SEGMENT", "merge_threshold_sec", fallback=6))

    # KONDENS
    window.max_chars_spin.setValue(config.getint("KONDENS", "max_chars", fallback=37))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DrOrkestrator()
    window.show()
    sys.exit(app.exec_())