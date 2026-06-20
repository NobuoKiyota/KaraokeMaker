import sys
import os
import threading
import json
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot, QEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QSlider, QProgressBar, QCheckBox,
    QComboBox, QSpinBox, QGroupBox, QFormLayout, QTextEdit, QSplitter,
    QColorDialog, QLineEdit, QTableWidget, QTableWidgetItem, QDockWidget,
    QHeaderView, QMessageBox, QTabWidget, QScrollArea
)
from PySide6.QtGui import QImage, QPixmap, QIcon, QFont, QColor
import openpyxl

# 内部モジュールをインポート
from karaoke_parser import KaraokeParser
from karaoke_renderer import KaraokeRenderer, StyleProfile
from audio_player import AudioPlayer
from video_exporter import VideoExporter

SETTINGS_FILE = r"Z:\KaraokeMaker\style_settings.json"

class ExportWorker(QThread):
    progress = Signal(int, int, float)  # current, total, speed
    finished = Signal(str)              # output_path
    failed = Signal(str)                # error message

    def __init__(self, exporter, output_path, audio_path, fps, width, height):
        super().__init__()
        self.exporter = exporter
        self.output_path = output_path
        self.audio_path = audio_path
        self.fps = fps
        self.width = width
        self.height = height

    def run(self):
        try:
            def callback(curr, tot, speed):
                self.progress.emit(curr, tot, speed)

            self.exporter.export(
                self.output_path,
                self.audio_path,
                fps=self.fps,
                width=self.width,
                height=self.height,
                progress_callback=callback
            )
            self.finished.emit(self.output_path)
        except Exception as e:
            self.failed.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KaraokeMaker Pro - カラオケ＆バンド練習動画エディター")
        self.resize(1500, 950)

        # コアコンポーネントの初期化
        self.player = AudioPlayer()
        self.player.set_position_changed_callback(self._on_audio_position_changed)
        self.parser = None
        self.renderer = None
        self.exporter = None
        self.style_profile = StyleProfile()

        # 設定のロード
        self._load_settings()

        # UIの構築
        self._init_ui()
        
        # 設定をUIに適用
        self._apply_settings_to_ui()
        
        # プレビュー更新タイマー (30 FPS)
        self.timer = QTimer()
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._update_preview)

        # 状態フラグ
        self.is_slider_pressed = False
        self.updating_table_from_code = False

    def _init_ui(self):
        # メイン領域 (Central Widget) はプレビュー画面と再生操作
        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(10, 10, 10, 10)
        central_layout.setSpacing(10)

        # 1. 曲メタデータ (アプリヘッダ部分)
        meta_layout = QHBoxLayout()
        self.lbl_title = QLabel("未ロード")
        self.lbl_title.setFont(QFont("Meiryo", 13, QFont.Bold))
        self.lbl_tempo_key = QLabel("BPM: -- | Key: --")
        self.lbl_tempo_key.setFont(QFont("Meiryo", 11))
        meta_layout.addWidget(self.lbl_title)
        meta_layout.addStretch()
        meta_layout.addWidget(self.lbl_tempo_key)
        central_layout.addLayout(meta_layout)

        # 2. プレビューラベル (動画フレーム描画先)
        self.lbl_preview = QLabel()
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet("background-color: #000000; border: 2px solid #1a1a1a; border-radius: 4px;")
        self.lbl_preview.setMinimumSize(480, 270)
        self.lbl_preview.installEventFilter(self)
        central_layout.addWidget(self.lbl_preview, 1)

        # 3. タイムラインと情報
        timeline_info_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00 / 00:00")
        self.lbl_bar_beat = QLabel("Bar: 1 | Beat: 1")
        timeline_info_layout.addWidget(self.lbl_current_time)
        timeline_info_layout.addStretch()
        timeline_info_layout.addWidget(self.lbl_bar_beat)
        central_layout.addLayout(timeline_info_layout)

        self.slider_time = QSlider(Qt.Horizontal)
        self.slider_time.setEnabled(False)
        self.slider_time.sliderPressed.connect(self._on_slider_pressed)
        self.slider_time.sliderReleased.connect(self._on_slider_released)
        self.slider_time.sliderMoved.connect(self._on_slider_moved)
        central_layout.addWidget(self.slider_time)

        # 4. コントロールボタン
        control_layout = QHBoxLayout()
        self.btn_play = QPushButton("再生")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_play.setEnabled(False)
        
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self._stop_play)
        self.btn_stop.setEnabled(False)

        control_layout.addStretch()
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(self.btn_stop)
        control_layout.addStretch()
        central_layout.addLayout(control_layout)

        # 5. 進捗バー
        self.progress_export = QProgressBar()
        self.progress_export.setVisible(False)
        self.progress_export.setValue(0)
        central_layout.addWidget(self.progress_export)

        self.setCentralWidget(central_widget)

        # ==========================================
        # QDockWidget の実装
        # ==========================================
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AnimatedDocks)

        # A. 左ドック: 設定・ファイル管理・スタイル（TabWidgetで整理）
        self.dock_config = QDockWidget("設定とスタイル", self)
        self.dock_config.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.dock_config.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        
        config_tab_widget = QTabWidget()

        # 共通スピンボックス生成ヘルパー
        def create_param_group(title_name, key_name, x_label="開始横幅(X)", y_label="開始高さ(Y)"):
            group = QGroupBox(title_name)
            layout = QFormLayout(group)
            layout.setSpacing(4)
            layout.setContentsMargins(6, 8, 6, 6)

            spin_size = QSpinBox()
            spin_size.setRange(1, 200)
            spin_size.valueChanged.connect(self._style_changed)
            layout.addRow("サイズ:", spin_size)
            setattr(self, f"spin_{key_name}_size", spin_size)

            spin_x = QSpinBox()
            spin_x.setRange(-200, 1920)
            spin_x.valueChanged.connect(self._style_changed)
            layout.addRow(f"{x_label}:", spin_x)
            setattr(self, f"spin_{key_name}_x", spin_x)

            spin_y = QSpinBox()
            spin_y.setRange(-200, 1080)
            spin_y.valueChanged.connect(self._style_changed)
            layout.addRow(f"{y_label}:", spin_y)
            setattr(self, f"spin_{key_name}_y", spin_y)

            return group

        # --- タブ1: 歌詞とコードの設定 ---
        tab_lyric_scroll = QScrollArea()
        tab_lyric_content = QWidget()
        lyric_layout = QVBoxLayout(tab_lyric_content)
        lyric_layout.setContentsMargins(6, 6, 6, 6)
        lyric_layout.setSpacing(8)

        # ファイル入力グループ
        file_group = QGroupBox("入力ファイル")
        file_layout = QFormLayout(file_group)
        file_layout.setSpacing(6)
        self.btn_select_excel = QPushButton("Excel選択...")
        self.btn_select_excel.clicked.connect(self._select_excel)
        self.lbl_excel_path = QLabel("未選択")
        self.lbl_excel_path.setWordWrap(True)
        file_layout.addRow(self.btn_select_excel, self.lbl_excel_path)
        
        self.btn_new_excel = QPushButton("テンプレートから新規作成...")
        self.btn_new_excel.clicked.connect(self._new_excel_from_template)
        file_layout.addRow(self.btn_new_excel, QLabel(""))
        
        self.btn_select_wav = QPushButton("WAV選択...")
        self.btn_select_wav.clicked.connect(self._select_wav)
        self.lbl_wav_path = QLabel("未選択")
        self.lbl_wav_path.setWordWrap(True)
        file_layout.addRow(self.btn_select_wav, self.lbl_wav_path)
        lyric_layout.addWidget(file_group)

        # 基本設定
        basic_group = QGroupBox("基本設定")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setSpacing(6)

        # フォント選択
        self.combo_lyric_font = QComboBox()
        self.combo_lyric_font.addItems(["メイリオ", "游ゴシック", "MS ゴシック", "Yu Gothic UI", "カスタムフォントファイル..."])
        self.combo_lyric_font.currentIndexChanged.connect(self._on_lyric_font_changed)
        self.btn_custom_lyric_font = QPushButton("フォントファイル選択...")
        self.btn_custom_lyric_font.clicked.connect(self._select_custom_lyric_font)
        self.btn_custom_lyric_font.setVisible(False)
        basic_layout.addRow("書体(フォント):", self.combo_lyric_font)
        basic_layout.addRow("", self.btn_custom_lyric_font)

        # カラー選択
        self.btn_color_idle = QPushButton("待機色 選択")
        self.btn_color_idle.clicked.connect(lambda: self._select_color("idle"))
        self.btn_color_active = QPushButton("歌唱後色 選択")
        self.btn_color_active.clicked.connect(lambda: self._select_color("active"))
        color_layout = QHBoxLayout()
        color_layout.addWidget(self.btn_color_idle)
        color_layout.addWidget(self.btn_color_active)
        basic_layout.addRow("歌詞カラー:", color_layout)

        # コードカラー選択
        self.btn_color_chord_idle = QPushButton("待機色 選択")
        self.btn_color_chord_idle.clicked.connect(lambda: self._select_color("chord_idle"))
        self.btn_color_chord_active = QPushButton("活性色 選択")
        self.btn_color_chord_active.clicked.connect(lambda: self._select_color("chord_active"))
        chord_color_layout = QHBoxLayout()
        chord_color_layout.addWidget(self.btn_color_chord_idle)
        chord_color_layout.addWidget(self.btn_color_chord_active)
        basic_layout.addRow("コードカラー:", chord_color_layout)

        self.spin_lyric_outline = QSpinBox()
        self.spin_lyric_outline.setRange(0, 15)
        self.spin_lyric_outline.setValue(self.style_profile.lyric_outline_width)
        self.spin_lyric_outline.valueChanged.connect(self._style_changed)
        basic_layout.addRow("歌詞縁取り幅 (px):", self.spin_lyric_outline)

        self.chk_score_layout = QCheckBox("楽譜風アライメント (歌詞の真上にコード表示)")
        self.chk_score_layout.setChecked(self.style_profile.show_score_layout)
        self.chk_score_layout.stateChanged.connect(self._style_changed)
        basic_layout.addRow(self.chk_score_layout)
        lyric_layout.addWidget(basic_group)

        # 歌詞＆コードの位置調整グループ (統一フォーマット)
        lyric_layout.addWidget(create_param_group("【歌詞①】", "lyric1", x_label="左右微調整(X)", y_label="開始高さ(Y)"))
        lyric_layout.addWidget(create_param_group("【コード①】", "chord1", x_label="左右微調整(X)", y_label="上オフセット(Y)"))
        lyric_layout.addWidget(create_param_group("【歌詞②】", "lyric2", x_label="左右微調整(X)", y_label="開始高さ(Y)"))
        lyric_layout.addWidget(create_param_group("【コード②】", "chord2", x_label="左右微調整(X)", y_label="上オフセット(Y)"))

        tab_lyric_scroll.setWidget(tab_lyric_content)
        tab_lyric_scroll.setWidgetResizable(True)
        config_tab_widget.addTab(tab_lyric_scroll, "歌詞＆コード")

        # --- タブ2: 動画内UIとメタデータ配置 ---
        tab_ui = QScrollArea()
        tab_ui_content = QWidget()
        ui_layout = QVBoxLayout(tab_ui_content)
        ui_layout.setContentsMargins(6, 6, 6, 6)
        ui_layout.setSpacing(8)

        # 表示オン・オフのチェックボックス群
        vis_group = QGroupBox("表示オン・オフ")
        vis_layout = QFormLayout(vis_group)
        vis_layout.setSpacing(6)

        self.chk_show_title = QCheckBox("動画内に曲名とBPM/Keyを表示")
        self.chk_show_title.setChecked(self.style_profile.show_title_in_video)
        self.chk_show_title.stateChanged.connect(self._style_changed)
        vis_layout.addRow(self.chk_show_title)

        self.chk_show_sec = QCheckBox("セクション名を表示")
        self.chk_show_sec.setChecked(self.style_profile.show_section_name)
        self.chk_show_sec.stateChanged.connect(self._style_changed)
        vis_layout.addRow(self.chk_show_sec)

        self.chk_metro = QCheckBox("メトロノーム(4拍ビート)を表示")
        self.chk_metro.setChecked(self.style_profile.show_metronome)
        self.chk_metro.stateChanged.connect(self._style_changed)
        vis_layout.addRow(self.chk_metro)

        self.chk_meas = QCheckBox("右上小節番号 (Bar:) を表示")
        self.chk_meas.setChecked(self.style_profile.show_measure_number)
        self.chk_meas.stateChanged.connect(self._style_changed)
        vis_layout.addRow(self.chk_meas)

        self.chk_show_timecode = QCheckBox("動画内にタイムコードを表示")
        self.chk_show_timecode.setChecked(self.style_profile.show_timecode)
        self.chk_show_timecode.stateChanged.connect(self._style_changed)
        vis_layout.addRow(self.chk_show_timecode)

        ui_layout.addWidget(vis_group)

        # UI要素のパラメータグループ群 (統一フォーマット)
        ui_layout.addWidget(create_param_group("【曲名】", "title", x_label="左余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【BPM】", "bpm", x_label="右余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【KEY】", "key", x_label="右余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【セクション】", "section", x_label="左余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【メトロノーム】", "metronome", x_label="右余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【小節番号】", "measure", x_label="右余白(X)", y_label="上余白(Y)"))
        ui_layout.addWidget(create_param_group("【タイムコード】", "timecode", x_label="右余白(X)", y_label="上余白(Y)"))

        # UIカラー設定グループ
        ui_color_group = QGroupBox("UIカラー設定")
        ui_color_layout = QFormLayout(ui_color_group)
        ui_color_layout.setSpacing(6)
        
        self.btn_color_title = QPushButton("タイトル色 選択")
        self.btn_color_title.clicked.connect(lambda: self._select_color("title"))
        
        self.btn_color_section = QPushButton("セクション色 選択")
        self.btn_color_section.clicked.connect(lambda: self._select_color("section"))

        self.btn_color_measure = QPushButton("小節番号色 選択")
        self.btn_color_measure.clicked.connect(lambda: self._select_color("measure"))

        self.btn_color_timecode = QPushButton("タイムコード色 選択")
        self.btn_color_timecode.clicked.connect(lambda: self._select_color("timecode"))

        self.btn_color_metro = QPushButton("メトロノーム(オン)色 選択")
        self.btn_color_metro.clicked.connect(lambda: self._select_color("metro_active"))

        ui_color_layout.addRow(self.btn_color_title)
        ui_color_layout.addRow(self.btn_color_section)
        ui_color_layout.addRow(self.btn_color_measure)
        ui_color_layout.addRow(self.btn_color_timecode)
        ui_color_layout.addRow(self.btn_color_metro)
        ui_layout.addWidget(ui_color_group)

        tab_ui.setWidget(tab_ui_content)
        tab_ui.setWidgetResizable(True)
        config_tab_widget.addTab(tab_ui, "UI配置＆メタ表示")

        # --- タブ3: 動画エクスポート ---
        tab_export = QWidget()
        export_form = QFormLayout(tab_export)
        export_form.setSpacing(8)

        export_group = QGroupBox("動画出力設定")
        export_layout = QFormLayout(export_group)
        export_layout.setSpacing(6)
        self.combo_size = QComboBox()
        self.combo_size.addItems(["1920 x 1080", "1280 x 720", "854 x 480"])
        self.combo_size.setCurrentText("1280 x 720")
        export_layout.addRow("解像度:", self.combo_size)
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["15", "30", "60"])
        self.combo_fps.setCurrentText("30")
        export_layout.addRow("フレームレート:", self.combo_fps)
        self.btn_export = QPushButton("動画を自動生成 (エクスポート)")
        self.btn_export.setStyleSheet("background-color: #00FF7F; color: #000000; font-weight: bold; padding: 8px;")
        self.btn_export.clicked.connect(self._export_video)
        export_layout.addRow(self.btn_export)
        export_form.addRow(export_group)

        config_tab_widget.addTab(tab_export, "エクスポート")

        self.dock_config.setWidget(config_tab_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock_config)

        # B. 下部・右ドック: タイミング編集テーブル (エディタ)
        self.dock_table = QDockWidget("タイミング編集エディタ (Excelと同期)", self)
        self.dock_table.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea | Qt.LeftDockWidgetArea)
        self.dock_table.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        
        table_widget = QWidget()
        table_layout = QVBoxLayout(table_widget)
        table_layout.setContentsMargins(5, 5, 5, 5)
        table_layout.setSpacing(5)

        btn_layout = QHBoxLayout()
        self.btn_add_row = QPushButton("行を挿入")
        self.btn_add_row.clicked.connect(self._add_table_row)
        self.btn_delete_row = QPushButton("選択行を削除")
        self.btn_delete_row.clicked.connect(self._delete_table_row)
        self.chk_instant_preview = QCheckBox("編集時に即時プレビュー反映")
        self.chk_instant_preview.setChecked(False)
        self.chk_instant_preview.stateChanged.connect(self._on_instant_preview_changed)
        self.btn_apply_table = QPushButton("編集内容を適用 (プレビュー更新)")
        self.btn_apply_table.setStyleSheet("background-color: #32CD32; color: #000000; font-weight: bold;")
        self.btn_apply_table.clicked.connect(self._apply_table_to_parser)
        self.btn_save_excel = QPushButton("Excelファイルを上書き保存")
        self.btn_save_excel.setStyleSheet("background-color: #1e90ff; color: white; font-weight: bold;")
        self.btn_save_excel.clicked.connect(self._save_to_excel)
        
        btn_layout.addWidget(self.btn_add_row)
        btn_layout.addWidget(self.btn_delete_row)
        btn_layout.addWidget(self.chk_instant_preview)
        btn_layout.addWidget(self.btn_apply_table)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save_excel)
        table_layout.addLayout(btn_layout)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(6)
        self.table_widget.setHorizontalHeaderLabels(["小節", "拍", "セクション", "コード", "歌詞", "表示行"])
        self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_widget.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table_widget.itemChanged.connect(self._on_table_item_changed)
        self.table_widget.itemSelectionChanged.connect(self._on_table_selection_changed)
        table_layout.addWidget(self.table_widget)

        self.dock_table.setWidget(table_widget)
        self.dock_table.setAllowedAreas(Qt.NoDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_table)
        self.dock_table.setFloating(True)
        self.dock_table.resize(1100, 600)

        # C. ログコンソールドック
        self.dock_log = QDockWidget("システムログ", self)
        self.dock_log.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("background-color: #181818; color: #8FBC8F; font-family: Consolas; font-size: 11px;")
        self.dock_log.setWidget(self.log_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.dock_log)

        self.tabifyDockWidget(self.dock_table, self.dock_log)

        # スタイリング (DAW風ダークテーマ)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #202020;
            }
            QDockWidget {
                color: #e0e0e0;
                font-weight: bold;
            }
            QDockWidget::title {
                background-color: #2b2b2b;
                padding: 4px;
                border-bottom: 1px solid #3c3c3c;
            }
            QGroupBox {
                color: #e0e0e0;
                font-weight: bold;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 8px;
                padding: 0 2px;
            }
            QLabel {
                color: #d0d0d0;
            }
            QPushButton {
                background-color: #333333;
                color: #d0d0d0;
                border: 1px solid #484848;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #444444;
                border-color: #585858;
            }
            QPushButton:pressed {
                background-color: #1e1e1e;
            }
            QSlider::groove:horizontal {
                border: 1px solid #3c3c3c;
                height: 5px;
                background: #151515;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #00FF7F;
                border: 1px solid #00AA55;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QComboBox, QSpinBox, QLineEdit {
                background-color: #333333;
                color: #d0d0d0;
                border: 1px solid #484848;
                border-radius: 4px;
                padding: 2px;
            }
            QProgressBar {
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                text-align: center;
                background-color: #151515;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #00FF7F;
                width: 8px;
            }
            QTableWidget {
                background-color: #282828;
                color: #e0e0e0;
                gridline-color: #3a3a3a;
                border: 1px solid #3a3a3a;
                font-family: Consolas, Meiryo;
                font-size: 12px;
            }
            QTableWidget::item:selected {
                background-color: #008B8B;
                color: white;
            }
            QHeaderView::section {
                background-color: #333333;
                color: #e0e0e0;
                border: 1px solid #222222;
                padding: 4px;
                font-weight: bold;
            }
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #2b2b2b;
            }
            QTabBar::tab {
                background-color: #202020;
                color: #a0a0a0;
                border: 1px solid #3c3c3c;
                padding: 6px 12px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #2b2b2b;
                color: #ffffff;
                border-bottom-color: #2b2b2b;
            }
        """)

    def _log(self, text):
        self.log_text.append(text)
        self.log_text.ensureCursorVisible()

    # ==========================================
    # 設定ファイル(JSON)保存・読込処理
    # ==========================================
    def _save_settings(self):
        try:
            d = self.style_profile.to_dict()
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print("Failed to save settings:", e)

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    self.style_profile.from_dict(d)
            except Exception as e:
                print("Failed to load settings:", e)

    def _apply_settings_to_ui(self):
        """ロードされた設定値を各GUIコントロールの初期値に適用する"""
        # フォントファミリーコンボの適用
        def set_font_combo(combo, btn, path):
            if not path:
                return
            if "meiryo.ttc" in path:
                combo.setCurrentText("メイリオ")
            elif "meiryob.ttc" in path:
                combo.setCurrentText("メイリオ")
            elif "msgothic.ttc" in path:
                combo.setCurrentText("MS ゴシック")
            elif "yu.ttc" in path or "yugothb.ttc" in path:
                combo.setCurrentText("游ゴシック")
            elif "msyh.ttc" in path:
                combo.setCurrentText("Yu Gothic UI")
            else:
                combo.setCurrentText("カスタムフォントファイル...")
                btn.setVisible(True)
                btn.setToolTip(path)
                btn.setText(os.path.basename(path))

        set_font_combo(self.combo_lyric_font, self.btn_custom_lyric_font, self.style_profile.lyric_font_path)

        # 歌詞アウトライン
        self.spin_lyric_outline.setValue(self.style_profile.lyric_outline_width)
        
        # チェックボックス類
        self.chk_score_layout.setChecked(self.style_profile.show_score_layout)
        self.chk_show_title.setChecked(self.style_profile.show_title_in_video)
        self.chk_show_sec.setChecked(self.style_profile.show_section_name)
        self.chk_metro.setChecked(self.style_profile.show_metronome)
        self.chk_meas.setChecked(self.style_profile.show_measure_number)
        self.chk_show_timecode.setChecked(self.style_profile.show_timecode)

        # 統一レイアウトパラメータを一括適用
        for key_name in self.style_profile.layout.keys():
            param = self.style_profile.layout[key_name]
            # size, x, y のスピンボックスに代入
            getattr(self, f"spin_{key_name}_size").setValue(int(param["size"]))
            getattr(self, f"spin_{key_name}_x").setValue(int(param["x"]))
            getattr(self, f"spin_{key_name}_y").setValue(int(param["y"]))

        # カラーボタンの背景色適用
        def set_btn_color(btn, hex_color):
            if hex_color:
                btn.setStyleSheet(f"background-color: {hex_color}; color: {'#000' if QColor(hex_color).lightness() > 128 else '#fff'};")
        set_btn_color(self.btn_color_idle, self.style_profile.lyric_idle_color)
        set_btn_color(self.btn_color_active, self.style_profile.lyric_active_color)
        set_btn_color(self.btn_color_chord_idle, self.style_profile.chord_idle_color)
        set_btn_color(self.btn_color_chord_active, self.style_profile.chord_active_color)
        set_btn_color(self.btn_color_title, self.style_profile.title_color)
        set_btn_color(self.btn_color_section, self.style_profile.section_color)
        set_btn_color(self.btn_color_measure, self.style_profile.measure_color)
        set_btn_color(self.btn_color_timecode, self.style_profile.timecode_color)
        set_btn_color(self.btn_color_metro, self.style_profile.metronome_active_color)

    # ==========================================
    # フォント変更イベント
    # ==========================================
    def _on_lyric_font_changed(self, index):
        text = self.combo_lyric_font.currentText()
        if text == "カスタムフォントファイル...":
            self.btn_custom_lyric_font.setVisible(True)
            self._select_custom_lyric_font()
        else:
            self.btn_custom_lyric_font.setVisible(False)
            paths = {
                "メイリオ": "C:\\Windows\\Fonts\\meiryo.ttc",
                "游ゴシック": "C:\\Windows\\Fonts\\yu.ttc",
                "MS ゴシック": "C:\\Windows\\Fonts\\msgothic.ttc",
                "Yu Gothic UI": "C:\\Windows\\Fonts\\yugothb.ttc"
            }
            self.style_profile.lyric_font_path = paths.get(text)
            self.style_profile.chord_font_path = paths.get(text)
            self.style_profile.ui_font_path = paths.get(text)
            self._style_changed()

    def _select_custom_lyric_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "フォントファイルを選択", "C:\\Windows\\Fonts", "Font Files (*.ttf *.ttc *.otf)")
        if path:
            self.style_profile.lyric_font_path = path
            self.style_profile.chord_font_path = path
            self.style_profile.ui_font_path = path
            self.btn_custom_lyric_font.setToolTip(path)
            self.btn_custom_lyric_font.setText(os.path.basename(path))
            self._style_changed()

    # ==========================================
    # ファイルロード処理
    # ==========================================
    def _new_excel_from_template(self):
        template_path = r"Z:\KaraokeMaker\Resources\karaokemaker_template.xlsx"
        if not os.path.exists(template_path):
            QMessageBox.critical(self, "エラー", "テンプレートファイルが見つかりません。")
            return
            
        new_path, _ = QFileDialog.getSaveFileName(self, "新規Excelファイルの保存先を指定", "Z:\\KaraokeMaker", "Excel Files (*.xlsx)")
        if not new_path:
            return
            
        try:
            import shutil
            import stat
            
            # コピーして新しいファイルを作成
            shutil.copy(template_path, new_path)
            
            # コピーしたファイルの読み取り専用属性を解除して、書き込み可能にする
            os.chmod(new_path, stat.S_IWRITE)
            
            # アプリにロードする
            self._load_excel_file(new_path)
            self._log(f"テンプレートから新規Excelを作成・ロードしました: {os.path.basename(new_path)}")
            QMessageBox.information(self, "完了", f"新規Excelファイルを作成し、エディタにロードしました！\n{new_path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"新規ファイルの作成に失敗しました:\n{e}")

    def _select_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "タイミングExcelファイルを選択", "Z:\\KaraokeMaker", "Excel Files (*.xlsx *.xls)")
        if path:
            self._load_excel_file(path)

    def _load_excel_file(self, path):
        try:
            self.lbl_excel_path.setText(os.path.basename(path))
            self.lbl_excel_path.setToolTip(path)
            self.parser = KaraokeParser(path)
            self.renderer = KaraokeRenderer(self.parser, self.style_profile)
            self.exporter = VideoExporter(self.renderer)

            title = self.parser.metadata.get("title", "Unknown")
            tempo = self.parser.metadata.get("tempo", 120.0)
            key = self.parser.metadata.get("key", "--")
            
            self.lbl_title.setText(f"{title}")
            self.lbl_tempo_key.setText(f"BPM: {tempo} | Key: {key}")
            
            self._populate_table_data()

            wav_name = self.parser.metadata.get("wav_path", "")
            if wav_name and wav_name.lower() not in ["none", "null", "参照選択", ""]:
                potential_wav = os.path.join(os.path.dirname(path), wav_name)
                if os.path.exists(potential_wav):
                    self._load_wav_file(potential_wav)
                else:
                    res_wav = os.path.join(os.path.dirname(path), "Resources", wav_name)
                    if os.path.exists(res_wav):
                        self._load_wav_file(res_wav)

            self._log(f"Excelをロードしました: {os.path.basename(path)}")
            self._update_preview()
        except Exception as e:
            self._log(f"Excelのロードに失敗しました: {e}")

    def _select_wav(self):
        path, _ = QFileDialog.getOpenFileName(self, "オーディオWAVファイルを選択", "Z:\\KaraokeMaker", "WAV Files (*.wav)")
        if path:
            self._load_wav_file(path)

    def _load_wav_file(self, path):
        try:
            self.lbl_wav_path.setText(os.path.basename(path))
            self.lbl_wav_path.setToolTip(path)
            self.player.load(path)
            
            duration_ms = int(self.player.get_duration() * 1000)
            self.slider_time.setRange(0, duration_ms)
            self.slider_time.setValue(0)
            self.slider_time.setEnabled(True)
            
            self.btn_play.setEnabled(True)
            self.btn_stop.setEnabled(True)
            
            self._log(f"WAVをロードしました: {os.path.basename(path)} (長さ: {self.player.get_duration():.2f} 秒)")
            self._update_time_label()
        except Exception as e:
            self._log(f"WAVのロードに失敗しました: {e}")

    # ==========================================
    # テーブル・エディタ関連処理
    # ==========================================
    def _populate_table_data(self):
        if not self.parser:
            return
            
        self.updating_table_from_code = True
        
        events = self.parser.raw_events
        self.table_widget.setRowCount(len(events))
        
        for row_idx, ev in enumerate(events):
            self.table_widget.setItem(row_idx, 0, QTableWidgetItem(str(ev["measure"])))
            self.table_widget.setItem(row_idx, 1, QTableWidgetItem(str(ev["beat"])))
            self.table_widget.setItem(row_idx, 2, QTableWidgetItem(str(ev["section"])))
            self.table_widget.setItem(row_idx, 3, QTableWidgetItem(str(ev["chord"])))
            self.table_widget.setItem(row_idx, 4, QTableWidgetItem(str(ev["lyric"])))
            self.table_widget.setItem(row_idx, 5, QTableWidgetItem(str(ev["line_num"])))
            
        self.updating_table_from_code = False

    def _on_table_item_changed(self, item):
        if self.updating_table_from_code or not self.parser:
            return
            
        if not self.chk_instant_preview.isChecked():
            # 即時反映がOFFの場合は何もしない（「適用」ボタン押下時にパース・更新を行う）
            return
            
        new_events = []
        tempo = self.parser.metadata["tempo"]
        beat_duration = 60.0 / tempo
        
        for r in range(self.table_widget.rowCount()):
            try:
                measure = int(self.table_widget.item(r, 0).text())
                beat = float(self.table_widget.item(r, 1).text())
                section = self.table_widget.item(r, 2).text().strip() if self.table_widget.item(r, 2) else ""
                chord = self.table_widget.item(r, 3).text().strip() if self.table_widget.item(r, 3) else ""
                lyric = self.table_widget.item(r, 4).text().strip() if self.table_widget.item(r, 4) else ""
                line_num = int(self.table_widget.item(r, 5).text()) if self.table_widget.item(r, 5) else 1
                
                time_sec = ((measure - 1) * 4 + (beat - 1)) * beat_duration
                
                new_events.append({
                    "measure": measure,
                    "beat": beat,
                    "section": section,
                    "chord": chord,
                    "lyric": lyric,
                    "line_num": line_num,
                    "time_sec": time_sec
                })
            except Exception:
                pass
                
        if new_events:
            new_events.sort(key=lambda x: x["time_sec"])
            self.parser.raw_events = new_events
            self.parser.parse(from_raw_events=True)
            self._update_preview()

    def _on_instant_preview_changed(self, state):
        # 2はQt.Checkedに対応
        if state == 2 or state == True:
            self.btn_apply_table.setEnabled(False)
            self._apply_table_to_parser()
        else:
            self.btn_apply_table.setEnabled(True)

    def _apply_table_to_parser(self):
        if not self.parser:
            return
            
        new_events = []
        tempo = self.parser.metadata["tempo"]
        beat_duration = 60.0 / tempo
        
        for r in range(self.table_widget.rowCount()):
            try:
                m_item = self.table_widget.item(r, 0)
                b_item = self.table_widget.item(r, 1)
                s_item = self.table_widget.item(r, 2)
                c_item = self.table_widget.item(r, 3)
                l_item = self.table_widget.item(r, 4)
                ln_item = self.table_widget.item(r, 5)
                
                measure = int(m_item.text()) if m_item else 1
                beat = float(b_item.text()) if b_item else 1.0
                section = s_item.text().strip() if s_item else ""
                chord = c_item.text().strip() if c_item else ""
                lyric = l_item.text().strip() if l_item else ""
                line_num = int(ln_item.text()) if ln_item else 1
                
                time_sec = ((measure - 1) * 4 + (beat - 1)) * beat_duration
                
                new_events.append({
                    "measure": measure,
                    "beat": beat,
                    "section": section,
                    "chord": chord,
                    "lyric": lyric,
                    "line_num": line_num,
                    "time_sec": time_sec
                })
            except Exception:
                pass
                
        if new_events:
            new_events.sort(key=lambda x: x["time_sec"])
            self.parser.raw_events = new_events
            self.parser.parse(from_raw_events=True)
            self._populate_table_data()
            self._update_preview()
            self._log("テーブル編集内容を適用しました")

    def _on_table_selection_changed(self):
        if self.updating_table_from_code or not self.parser or self.player.is_playing():
            return
            
        selected_ranges = self.table_widget.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        
        try:
            measure = int(self.table_widget.item(row, 0).text())
            beat = float(self.table_widget.item(row, 1).text())
            
            tempo = self.parser.metadata["tempo"]
            beat_duration = 60.0 / tempo
            time_sec = ((measure - 1) * 4 + (beat - 1)) * beat_duration
            
            self.player.seek(time_sec)
            self._update_preview()
        except Exception:
            pass

    def _on_audio_position_changed(self, time_sec):
        if not self.parser or self.updating_table_from_code or self.is_slider_pressed:
            return
            
        events = self.parser.raw_events
        closest_row = -1
        min_diff = 999.0
        
        for i, ev in enumerate(events):
            diff = abs(ev["time_sec"] - time_sec)
            if diff < min_diff:
                min_diff = diff
                closest_row = i
                
        if closest_row != -1 and min_diff < 0.5:
            self.updating_table_from_code = True
            self.table_widget.selectRow(closest_row)
            self.updating_table_from_code = False

    def _add_table_row(self):
        row = self.table_widget.currentRow()
        if row == -1:
            row = self.table_widget.rowCount() - 1
            
        insert_idx = row + 1
        self.table_widget.insertRow(insert_idx)
        
        try:
            prev_m = self.table_widget.item(row, 0).text()
            prev_b = self.table_widget.item(row, 1).text()
            prev_s = self.table_widget.item(row, 2).text()
            prev_l = self.table_widget.item(row, 5).text()
        except Exception:
            prev_m, prev_b, prev_s, prev_l = "1", "1", "Intro", "1"

        self.updating_table_from_code = True
        self.table_widget.setItem(insert_idx, 0, QTableWidgetItem(prev_m))
        try:
            next_b = float(prev_b) + 1.0
            if next_b > 4.0:
                next_b = 1.0
                prev_m = str(int(prev_m) + 1)
                self.table_widget.setItem(insert_idx, 0, QTableWidgetItem(prev_m))
            self.table_widget.setItem(insert_idx, 1, QTableWidgetItem(str(next_b)))
        except ValueError:
            self.table_widget.setItem(insert_idx, 1, QTableWidgetItem("1"))
            
        self.table_widget.setItem(insert_idx, 2, QTableWidgetItem(prev_s))
        self.table_widget.setItem(insert_idx, 3, QTableWidgetItem(""))
        self.table_widget.setItem(insert_idx, 4, QTableWidgetItem("none"))
        self.table_widget.setItem(insert_idx, 5, QTableWidgetItem(prev_l))
        
        self.updating_table_from_code = False
        self._on_table_item_changed(None)

    def _delete_table_row(self):
        row = self.table_widget.currentRow()
        if row != -1:
            self.table_widget.removeRow(row)
            self._on_table_item_changed(None)

    def _save_to_excel(self):
        if not self.parser:
            QMessageBox.warning(self, "警告", "Excelファイルがロードされていません。")
            return
            
        excel_path = self.parser.excel_path
        
        try:
            wb = openpyxl.load_workbook(excel_path)
            sheet = wb.active
            
            max_r = sheet.max_row
            if max_r >= 9:
                sheet.delete_rows(9, amount=max_r - 8)
                
            for r in range(self.table_widget.rowCount()):
                row_num = 9 + r
                for c in range(6):
                    val = self.table_widget.item(r, c).text()
                    try:
                        if c == 0 or c == 5:
                            val = int(val)
                        elif c == 1:
                            val = float(val)
                    except ValueError:
                        pass
                    sheet.cell(row=row_num, column=c+1, value=val)
                    
            end_row = 9 + self.table_widget.rowCount()
            sheet.cell(row=end_row, column=1, value="End")
            
            wb.save(excel_path)
            self._log(f"Excelを上書き保存しました: {excel_path}")
            QMessageBox.information(self, "完了", "Excelファイルを上書き保存しました。")
        except Exception as e:
            self._log(f"Excelの保存に失敗しました: {e}")
            QMessageBox.critical(self, "エラー", f"Excelの保存に失敗しました:\n{e}")

    # ==========================================
    # プレビュー描画処理
    # ==========================================
    def eventFilter(self, watched, event):
        if watched == self.lbl_preview and event.type() == QEvent.Resize:
            self._update_preview()
        return super().eventFilter(watched, event)

    def _update_preview(self):
        if not self.renderer:
            return
            
        time_sec = self.player.get_time()
        
        lbl_w = self.lbl_preview.width()
        lbl_h = self.lbl_preview.height()
        
        if lbl_w < 100 or lbl_h < 100:
            return
            
        ratio = 16.0 / 9.0
        if lbl_w / lbl_h > ratio:
            render_h = lbl_h
            render_w = int(lbl_h * ratio)
        else:
            render_w = lbl_w
            render_h = int(lbl_w / ratio)

        try:
            pil_img = self.renderer.render_frame(time_sec, render_w, render_h)
            
            raw_data = pil_img.tobytes("raw", "RGB")
            qimg = QImage(raw_data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qimg)
            
            self.lbl_preview.setPixmap(pixmap)
            
            if not self.is_slider_pressed:
                self.slider_time.setValue(int(time_sec * 1000))
            self._update_time_label()
            
        except Exception as e:
            pass

    def _update_time_label(self):
        time_sec = self.player.get_time()
        dur_sec = self.player.get_duration()
        
        cur_min, cur_sec = divmod(int(time_sec), 60)
        dur_min, dur_sec = divmod(int(dur_sec), 60)
        
        self.lbl_current_time.setText(f"{cur_min:02d}:{cur_sec:02d} / {dur_min:02d}:{dur_sec:02d}")
        
        if self.parser:
            beat_info = self.parser.get_current_beat_info(time_sec)
            self.lbl_bar_beat.setText(f"Bar: {beat_info['measure']} | Beat: {beat_info['beat']}")

    # ==========================================
    # 再生コントロール
    # ==========================================
    def _toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.timer.stop()
            self.btn_play.setText("再生")
            self._log("再生を一時停止しました")
        else:
            self.player.play()
            self.timer.start()
            self.btn_play.setText("一時停止")
            self._log("再生を開始しました")

    def _stop_play(self):
        self.player.stop()
        self.timer.stop()
        self.btn_play.setText("再生")
        self.slider_time.setValue(0)
        self._update_preview()
        self._log("再生を停止しました")

    # ==========================================
    # タイムラインシーク処理
    # ==========================================
    def _on_slider_pressed(self):
        self.is_slider_pressed = True

    def _on_slider_released(self):
        self.is_slider_pressed = False
        val_sec = self.slider_time.value() / 1000.0
        self.player.seek(val_sec)
        self._update_preview()
        self._log(f"シークしました: {val_sec:.2f}秒")

    def _on_slider_moved(self, value):
        val_sec = value / 1000.0
        self.player.seek(val_sec)
        self._update_preview()

    # ==========================================
    # スタイル・設定変更
    # ==========================================
    def _style_changed(self):
        if not self.renderer:
            return
            
        # 統一レイアウトスピンボックスから辞書へ値を格納
        for key_name in self.style_profile.layout.keys():
            self.style_profile.layout[key_name]["size"] = getattr(self, f"spin_{key_name}_size").value()
            self.style_profile.layout[key_name]["x"] = getattr(self, f"spin_{key_name}_x").value()
            self.style_profile.layout[key_name]["y"] = getattr(self, f"spin_{key_name}_y").value()
            
        # チェックボックスなどの適用
        self.style_profile.show_score_layout = self.chk_score_layout.isChecked()
        self.style_profile.show_metronome = self.chk_metro.isChecked()
        self.style_profile.show_title_in_video = self.chk_show_title.isChecked()
        self.style_profile.show_section_name = self.chk_show_sec.isChecked()
        self.style_profile.show_measure_number = self.chk_meas.isChecked()
        self.style_profile.show_timecode = self.chk_show_timecode.isChecked()
        
        self.style_profile.lyric_outline_width = self.spin_lyric_outline.value()
        
        # 保存と再描画
        self._save_settings()
        self._update_preview()

    def _select_color(self, target):
        color = QColorDialog.getColor()
        if color.isValid():
            hex_color = color.name()
            text_style = f"background-color: {hex_color}; color: {'#000' if color.lightness() > 128 else '#fff'};"
            if target == "idle":
                self.style_profile.lyric_idle_color = hex_color
                self.btn_color_idle.setStyleSheet(text_style)
            elif target == "active":
                self.style_profile.lyric_active_color = hex_color
                self.btn_color_active.setStyleSheet(text_style)
            elif target == "chord_idle":
                self.style_profile.chord_idle_color = hex_color
                self.btn_color_chord_idle.setStyleSheet(text_style)
            elif target == "chord_active":
                self.style_profile.chord_active_color = hex_color
                self.btn_color_chord_active.setStyleSheet(text_style)
            elif target == "title":
                self.style_profile.title_color = hex_color
                self.btn_color_title.setStyleSheet(text_style)
            elif target == "section":
                self.style_profile.section_color = hex_color
                self.btn_color_section.setStyleSheet(text_style)
            elif target == "measure":
                self.style_profile.measure_color = hex_color
                self.btn_color_measure.setStyleSheet(text_style)
            elif target == "timecode":
                self.style_profile.timecode_color = hex_color
                self.btn_color_timecode.setStyleSheet(text_style)
            elif target == "metro_active":
                self.style_profile.metronome_active_color = hex_color
                self.btn_color_metro.setStyleSheet(text_style)
            
            self._save_settings()
            self._update_preview()

    # ==========================================
    # 動画エクスポート処理
    # ==========================================
    def _export_video(self):
        if not self.parser or not self.renderer:
            self._log("エラー: Excelファイルがロードされていません")
            return
            
        wav_path = self.lbl_wav_path.toolTip()
        if not wav_path or wav_path == "未選択" or not os.path.exists(wav_path):
            wav_path = r"Z:\KaraokeMaker\Resources\test.wav"
            
        if not os.path.exists(wav_path):
            self._log(f"エラー: 音声WAVファイルが見つかりません: {wav_path}")
            return

        default_out = os.path.join(os.path.dirname(self.parser.excel_path), "output.mp4")
        out_path, _ = QFileDialog.getSaveFileName(self, "書き出し動画の保存先を指定", default_out, "MP4 Video (*.mp4)")
        
        if not out_path:
            return

        size_str = self.combo_size.currentText()
        parts = size_str.split("x")
        w = int(parts[0].strip())
        h = int(parts[1].strip())
        fps = int(self.combo_fps.currentText())

        # 再生停止
        self._stop_play()

        # UI操作の無効化
        self._set_ui_enabled(False)
        self.progress_export.setVisible(True)
        self.progress_export.setValue(0)
        self._log("動画エクスポートを開始します...")

        # 別スレッドでエクスポート開始
        self.worker = ExportWorker(self.exporter, out_path, wav_path, fps, w, h)
        self.worker.progress.connect(self._on_export_progress)
        self.worker.finished.connect(self._on_export_finished)
        self.worker.failed.connect(self._on_export_failed)
        self.worker.start()

    def _on_export_progress(self, current, total, speed):
        self.progress_export.setValue(current)
        self.progress_export.setMaximum(total)
        self._log(f"エクスポート中: {current}/{total} フレーム ({speed:.1f} fps)")

    def _on_export_finished(self, out_path):
        self._set_ui_enabled(True)
        self.progress_export.setVisible(False)
        self._log(f"動画エクスポートが完了しました: {out_path}")
        QMessageBox.information(self, "完了", f"動画の書き出しが完了しました！\n保存先: {out_path}")

    def _on_export_failed(self, err_msg):
        self._set_ui_enabled(True)
        self.progress_export.setVisible(False)
        self._log(f"エラー: 動画エクスポートに失敗しました: {err_msg}")
        QMessageBox.critical(self, "エラー", f"動画の書き出しに失敗しました:\n{err_msg}")

    def _set_ui_enabled(self, enabled):
        self.btn_new_excel.setEnabled(enabled)
        self.btn_select_excel.setEnabled(enabled)
        self.btn_select_wav.setEnabled(enabled)
        self.btn_export.setEnabled(enabled)
        self.combo_size.setEnabled(enabled)
        self.combo_fps.setEnabled(enabled)
        self.spin_lyric_outline.setEnabled(enabled)
        self.btn_color_idle.setEnabled(enabled)
        self.btn_color_active.setEnabled(enabled)
        self.btn_play.setEnabled(enabled)
        self.btn_stop.setEnabled(enabled)
        self.slider_time.setEnabled(enabled)
        self.btn_add_row.setEnabled(enabled)
        self.btn_delete_row.setEnabled(enabled)
        self.btn_save_excel.setEnabled(enabled)
        self.table_widget.setEnabled(enabled)
        
        # 統一パラメータスピンボックス類のロック・アンロック
        for key_name in self.style_profile.layout.keys():
            getattr(self, f"spin_{key_name}_size").setEnabled(enabled)
            getattr(self, f"spin_{key_name}_x").setEnabled(enabled)
            getattr(self, f"spin_{key_name}_y").setEnabled(enabled)

    def closeEvent(self, event):
        self._save_settings()
        self.player.stop()
        self.timer.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
