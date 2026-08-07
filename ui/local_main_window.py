"""本地版（离线）主窗口。"""
import os

from qfluentwidgets import FluentIcon as FIF, FluentWindow


class LocalMainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.navigationInterface.setReturnButtonVisible(False)
        self.setWindowTitle("MS JSON 导出工具（本地版）")
        self.resize(1000, 700)
        self.setMinimumSize(1000, 700)

        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(icon_path))

        from ui.local_pages.local_metadata_export_page import LocalMetadataExportPage
        self.metadata_export_page = LocalMetadataExportPage(self)
        self.metadata_export_page.setObjectName("localMetadataExportInterface")
        self.addSubInterface(self.metadata_export_page, FIF.INFO, "元数据提取")

        from ui.local_pages.local_audio_download_page import LocalAudioDownloadPage
        self.audio_download_page = LocalAudioDownloadPage(self)
        self.audio_download_page.setObjectName("localAudioDownloadInterface")
        self.addSubInterface(self.audio_download_page, FIF.DOWNLOAD, "音频导出")

        from ui.local_pages.local_lyric_export_page import LocalLyricExportPage
        self.lyric_export_page = LocalLyricExportPage(self)
        self.lyric_export_page.setObjectName("localLyricExportInterface")
        self.addSubInterface(self.lyric_export_page, FIF.FONT, "歌词导出")

        from ui.local_pages.local_midi_export_page import LocalMidiExportPage
        self.midi_export_page = LocalMidiExportPage(self)
        self.midi_export_page.setObjectName("localMidiExportInterface")
        self.addSubInterface(self.midi_export_page, FIF.MUSIC, "MIDI导出")
