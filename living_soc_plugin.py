"""
=========================================================================
Living SOC Analyzer - QGIS Plugin Main Class (v3.0)
=========================================================================
QGIS 인터페이스 연결: 메뉴 등록, 툴바 아이콘, 대화상자 관리
=========================================================================
"""
import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction


class LivingSOCPlugin:
    """QGIS Plugin 메인 클래스"""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "Living SOC Analyzer"
        self.toolbar = self.iface.addToolBar("Living SOC Analyzer")
        self.toolbar.setObjectName("LivingSOCAnalyzerV3")
        self.dlg = None

    def initGui(self):
        """플러그인 GUI 초기화 (QGIS 시작 시 호출)"""
        icon_path = os.path.join(self.plugin_dir, "icons", "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        # 메인: 분석 대시보드
        self._add_action(icon, "Living SOC 분석 대시보드",
                         self.run_main, add_toolbar=True)

        # 빠른 실행: 전체 파이프라인
        self._add_action(QIcon(), "▶ 12단계 전체 실행",
                         self.run_full_pipeline)

        # 설정
        self._add_action(QIcon(), "⚙ API 키 설정", self.run_settings)

    def _add_action(self, icon, text, callback, add_toolbar=False):
        action = QAction(icon, text, self.iface.mainWindow())
        action.triggered.connect(callback)
        self.iface.addPluginToMenu(self.menu, action)
        if add_toolbar:
            self.iface.addToolBarIcon(action)
        self.actions.append(action)

    def unload(self):
        """플러그인 제거"""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        if self.toolbar:
            del self.toolbar
        self.dlg = None

    def _ensure_dialog(self):
        if self.dlg is None:
            from .living_soc_dialog import LivingSOCDialog
            self.dlg = LivingSOCDialog(self.iface)
        return self.dlg

    def run_main(self):
        dlg = self._ensure_dialog()
        dlg.show()
        dlg.raise_()

    def run_full_pipeline(self):
        dlg = self._ensure_dialog()
        dlg.show()
        dlg.raise_()
        dlg.tabs.setCurrentIndex(1)  # 실행 탭

    def run_settings(self):
        dlg = self._ensure_dialog()
        dlg.show()
        dlg.raise_()
        dlg.tabs.setCurrentIndex(0)  # 설정 탭
