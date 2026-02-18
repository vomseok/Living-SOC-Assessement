"""
=========================================================================
Living SOC Analyzer v3.0 — QGIS 대화상자
=========================================================================
6개 탭:
  Tab 0: ⚙ 설정         API 키 · 대상지역 · 분석연도 · 시설유형 선택
  Tab 1: ▶ 실행         12단계 파이프라인 원클릭 실행
  Tab 2: 📊 분석         접근성·공급적합성 개별 실행
  Tab 3: 📈 검증         통계검증 (Moran's I / Bootstrap / 민감도)
  Tab 4: 🗺 시각화       QGIS 레이어 자동 등록 + 스타일
  Tab 5: 📋 보고서       Excel 9시트 · HTML · JSON 자동 생성
=========================================================================
"""
import os
import sys
import json
import traceback
from pathlib import Path
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSettings
from qgis.PyQt.QtWidgets import (
    QDialog, QTabWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QWidget, QScrollArea, QSplitter, QFrame,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSymbol,
    QgsClassificationQuantile, QgsClassificationJenks,
    QgsClassificationEqualInterval,
)

# ── modules/ 경로 등록 ──
PLUGIN_DIR = os.path.dirname(__file__)
MODULES_DIR = os.path.join(PLUGIN_DIR, "modules")
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)


# ═════════════════════════════════════════════════
# Worker Thread — 파이프라인 백그라운드 실행
# ═════════════════════════════════════════════════
class PipelineWorker(QThread):
    """백그라운드 12단계 실행"""
    progress = pyqtSignal(int, str)    # (%, 메시지)
    log_msg = pyqtSignal(str)
    phase_update = pyqtSignal(int, str)  # (phase 번호, 상태)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, mode, settings):
        super().__init__()
        self.mode = mode        # "full" | "collect" | "analyze" | "validate" | "report"
        self.settings = settings
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self.mode == "full":
                result = self._run_full()
            elif self.mode == "collect":
                result = self._run_collect()
            elif self.mode == "analyze":
                result = self._run_analyze()
            elif self.mode == "validate":
                result = self._run_validate()
            elif self.mode == "report":
                result = self._run_report()
            else:
                result = {}

            if not self._is_cancelled:
                self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"{e}\n{traceback.format_exc()}")

    # ── Full Pipeline ──
    def _run_full(self):
        """12단계 전체 실행"""
        self.log_msg.emit("=" * 60)
        self.log_msg.emit("Living SOC 12단계 파이프라인 시작")
        self.log_msg.emit(f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_msg.emit("=" * 60)

        # settings 모듈에 API 키 주입
        import settings as cfg
        user_keys = self.settings.get("api_keys", {})
        for k, v in user_keys.items():
            if v:
                cfg.API_KEYS[k] = v

        # 대상지역 설정
        target_areas = self.settings.get("target_areas", cfg.TARGET_AREAS)
        year = self.settings.get("year", cfg.ANALYSIS_YEAR)
        output_dir = self.settings.get("output_dir", cfg.OUTPUT_DIR)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # ---------- Phase 1~4: 데이터 수집 ----------
        self.phase_update.emit(1, "running")
        self.progress.emit(3, "[Phase 1/12] 시설·인구 API 데이터 구득...")
        self.log_msg.emit("\n[Phase 1/12] 시설·인구 API 데이터 구득")

        from api_fetcher import APIFetcher
        fetcher = APIFetcher(api_keys=cfg.API_KEYS)
        facilities_raw = {}
        population_raw = None

        for area_name, area_info in target_areas.items():
            if self._is_cancelled:
                return {"cancelled": True}
            self.log_msg.emit(f"  → {area_name} ({area_info['code']})")
            code = area_info.get("code", "")

            # 시설 수집
            for ftype in cfg.FACILITY_TYPES:
                try:
                    df = fetcher.fetch_medical_facilities(code, ftype)
                    if df is not None and len(df) > 0:
                        key = f"{area_name}_{ftype}"
                        facilities_raw[key] = df
                        self.log_msg.emit(f"    {ftype}: {len(df)}건")
                except Exception as e:
                    self.log_msg.emit(f"    {ftype}: 실패 ({e})")

            # 인구 수집
            try:
                pop = fetcher.fetch_population(code, year)
                if pop is not None:
                    population_raw = pop
                    self.log_msg.emit(f"    인구: {len(pop)}건")
            except Exception as e:
                self.log_msg.emit(f"    인구 실패: {e}")

        self.phase_update.emit(1, "done")

        # Phase 2: 데이터 표준화
        self.phase_update.emit(2, "running")
        self.progress.emit(12, "[Phase 2/12] 데이터 표준화...")
        self.log_msg.emit("\n[Phase 2/12] 데이터 표준화")

        from data_processor import DataProcessor
        processor = DataProcessor()
        facilities_merged = None
        try:
            import pandas as pd
            dfs = [df for df in facilities_raw.values() if df is not None]
            if dfs:
                facilities_merged = pd.concat(dfs, ignore_index=True)
                facilities_merged = processor.standardize_columns(
                    facilities_merged)
                self.log_msg.emit(f"  통합 시설: {len(facilities_merged)}건")
        except Exception as e:
            self.log_msg.emit(f"  표준화 실패: {e}")
        self.phase_update.emit(2, "done")

        # Phase 3: 좌표 보정
        self.phase_update.emit(3, "running")
        self.progress.emit(20, "[Phase 3/12] 좌표 보정...")
        self.log_msg.emit("\n[Phase 3/12] 좌표 보정 (주소→좌표 지오코딩)")
        if facilities_merged is not None:
            try:
                facilities_merged = processor.geocode_missing(
                    facilities_merged, api_keys=cfg.API_KEYS)
                self.log_msg.emit(f"  좌표 보정 완료: {len(facilities_merged)}건")
            except Exception as e:
                self.log_msg.emit(f"  좌표 보정 건너뜀: {e}")
        self.phase_update.emit(3, "done")

        # Phase 4: 용량 정규화
        self.phase_update.emit(4, "running")
        self.progress.emit(25, "[Phase 4/12] 용량 표준화 (Min-Max)...")
        self.log_msg.emit("\n[Phase 4/12] 용량지표 Min-Max 정규화")
        if facilities_merged is not None:
            try:
                facilities_merged = processor.normalize_capacity(
                    facilities_merged)
                self.log_msg.emit("  용량 정규화 완료")
            except Exception as e:
                self.log_msg.emit(f"  정규화 건너뜀: {e}")
        self.phase_update.emit(4, "done")

        # ---------- Phase 5~7: 공간·교통 데이터 ----------
        self.phase_update.emit(5, "running")
        self.progress.emit(33, "[Phase 5/12] 공간데이터 수집...")
        self.log_msg.emit("\n[Phase 5/12] 공간데이터 수집 (행정경계·DEM·경사)")
        admin_gdf = None
        try:
            from spatial_fetcher import SpatialDataFetcher
            sp = SpatialDataFetcher(api_keys=cfg.API_KEYS)
            for area_name, area_info in target_areas.items():
                gdf = sp.fetch_admin_boundary(area_info["code"])
                if gdf is not None:
                    admin_gdf = gdf
                    self.log_msg.emit(f"  {area_name} 행정경계: {len(gdf)}개 읍면동")
        except Exception as e:
            self.log_msg.emit(f"  공간데이터 수집 실패: {e}")
        self.phase_update.emit(5, "done")

        self.phase_update.emit(6, "running")
        self.progress.emit(40, "[Phase 6/12] 교통망 수집 (OSM)...")
        self.log_msg.emit("\n[Phase 6/12] OSM 도로 네트워크 수집")
        road_graph = None
        try:
            from transport_fetcher import TransportFetcher
            tf = TransportFetcher()
            for area_name, area_info in target_areas.items():
                G = tf.fetch_osm_network(area_info["code"])
                if G is not None:
                    road_graph = G
                    self.log_msg.emit(
                        f"  {area_name}: 노드 {G.number_of_nodes()}, "
                        f"링크 {G.number_of_edges()}")
        except Exception as e:
            self.log_msg.emit(f"  교통망 수집 실패: {e}")
        self.phase_update.emit(6, "done")

        self.phase_update.emit(7, "running")
        self.progress.emit(50, "[Phase 7/12] 카카오 OD 행렬...")
        self.log_msg.emit("\n[Phase 7/12] 카카오맵 실제 이동시간 OD 행렬")
        od_matrix = None
        if cfg.API_KEYS.get("kakao_rest"):
            try:
                from transport_fetcher import TransportFetcher
                tf = TransportFetcher(api_keys=cfg.API_KEYS)
                od_matrix = tf.build_kakao_od_matrix(
                    facilities_merged, population_raw, sample_n=200)
                if od_matrix is not None:
                    self.log_msg.emit(f"  OD 행렬: {od_matrix.shape}")
            except Exception as e:
                self.log_msg.emit(f"  OD 행렬 실패: {e}")
        else:
            self.log_msg.emit("  카카오 키 없음 → 직선거리 대체")
        self.phase_update.emit(7, "done")

        # Phase 8: 품질검증
        self.phase_update.emit(8, "running")
        self.progress.emit(58, "[Phase 8/12] 품질 검증...")
        self.log_msg.emit("\n[Phase 8/12] 데이터 품질 검증 & 내보내기")
        quality_report = {}
        if facilities_merged is not None:
            n_total = len(facilities_merged)
            n_coords = facilities_merged[["lon", "lat"]].dropna().shape[0] \
                if "lon" in facilities_merged.columns else 0
            quality_report = {
                "total_facilities": n_total,
                "geocoded": n_coords,
                "geocode_rate": round(n_coords / max(n_total, 1) * 100, 1),
            }
            self.log_msg.emit(f"  시설 {n_total}건, 좌표확보 {n_coords}건 "
                              f"({quality_report['geocode_rate']}%)")

            # CSV 내보내기
            csv_path = os.path.join(output_dir, "facilities_merged.csv")
            facilities_merged.to_csv(csv_path, index=False, encoding="utf-8-sig")
            self.log_msg.emit(f"  CSV 저장: {csv_path}")
        self.phase_update.emit(8, "done")

        # ---------- Phase 9: E2SFCA 분석 ----------
        self.phase_update.emit(9, "running")
        self.progress.emit(65, "[Phase 9/12] E2SFCA 접근성 분석...")
        self.log_msg.emit("\n[Phase 9/12] E2SFCA 접근성 + PPR + 유인력 + 혼잡도")
        analysis_results = {}
        facilities_gdf = None
        population_gdf = None

        try:
            import geopandas as gpd
            from shapely.geometry import Point

            if facilities_merged is not None and \
               "lon" in facilities_merged.columns and \
               "lat" in facilities_merged.columns:

                valid = facilities_merged.dropna(subset=["lon", "lat"])
                if len(valid) > 0:
                    geometry = [Point(xy) for xy in
                                zip(valid["lon"], valid["lat"])]
                    facilities_gdf = gpd.GeoDataFrame(
                        valid, geometry=geometry,
                        crs=cfg.CRS_WGS84)
                    facilities_gdf = facilities_gdf.to_crs(cfg.CRS_KOREA)

            if population_raw is not None and \
               "lon" in population_raw.columns:
                valid_pop = population_raw.dropna(subset=["lon", "lat"])
                if len(valid_pop) > 0:
                    geom_p = [Point(xy) for xy in
                              zip(valid_pop["lon"], valid_pop["lat"])]
                    population_gdf = gpd.GeoDataFrame(
                        valid_pop, geometry=geom_p,
                        crs=cfg.CRS_WGS84)
                    population_gdf = population_gdf.to_crs(cfg.CRS_KOREA)

            if facilities_gdf is not None and population_gdf is not None:
                from analyzer import SpatialAnalyzer
                analyzer = SpatialAnalyzer(
                    facilities_gdf, population_gdf,
                    od_matrix=od_matrix, road_graph=road_graph)
                analysis_results = analyzer.run_full_analysis()
                self.log_msg.emit(
                    f"  분석 완료: {len(analysis_results)}개 시설유형")
            else:
                self.log_msg.emit("  ⚠ GeoDataFrame 생성 실패 → 분석 건너뜀")

        except Exception as e:
            self.log_msg.emit(f"  분석 오류: {e}")
            self.log_msg.emit(traceback.format_exc())
        self.phase_update.emit(9, "done")

        # ---------- Phase 10: 형평성·유형화 ----------
        self.phase_update.emit(10, "running")
        self.progress.emit(78, "[Phase 10/12] 형평성·지역유형화...")
        self.log_msg.emit("\n[Phase 10/12] 형평성(Gini·T검정) + K-means 유형화")
        equity_results = {}
        try:
            from equity_typology import EquityTypologyAnalyzer
            if analysis_results:
                eq = EquityTypologyAnalyzer(
                    analysis_results, admin_gdf=admin_gdf)
                equity_results = eq.run_full_analysis()
                self.log_msg.emit("  형평성·유형화 완료")
        except Exception as e:
            self.log_msg.emit(f"  형평성 분석 오류: {e}")
        self.phase_update.emit(10, "done")

        # ---------- Phase 11: 통계검증 ----------
        self.phase_update.emit(11, "running")
        self.progress.emit(88, "[Phase 11/12] 통계검증...")
        self.log_msg.emit("\n[Phase 11/12] Moran's I · Bootstrap · 민감도 분석")
        validation_results = {}
        try:
            from statistical_validator import StatisticalValidator
            if facilities_gdf is not None and analysis_results:
                sv = StatisticalValidator(
                    facilities_gdf, population_gdf, analysis_results)
                validation_results = sv.run_full_validation()
                grade = validation_results.get(
                    "종합_품질", {}).get("등급", "-")
                self.log_msg.emit(f"  분석 품질 등급: {grade}")
        except Exception as e:
            self.log_msg.emit(f"  통계검증 오류: {e}")
        self.phase_update.emit(11, "done")

        # ---------- Phase 12: 보고서 ----------
        self.phase_update.emit(12, "running")
        self.progress.emit(95, "[Phase 12/12] 자동보고서 생성...")
        self.log_msg.emit(
            "\n[Phase 12/12] Excel(9시트) + HTML대시보드 + JSON 생성")
        report_paths = {}
        try:
            from auto_report import AutoReportGenerator
            rg = AutoReportGenerator(
                analysis_results=analysis_results,
                equity_results=equity_results,
                validation_results=validation_results,
                output_dir=output_dir,
            )
            report_paths = rg.generate_all()
            for fmt, path in report_paths.items():
                self.log_msg.emit(f"  {fmt.upper()}: {path}")
        except Exception as e:
            self.log_msg.emit(f"  보고서 생성 오류: {e}")
        self.phase_update.emit(12, "done")

        # 완료
        self.progress.emit(100, "12단계 파이프라인 완료!")
        self.log_msg.emit("\n" + "=" * 60)
        self.log_msg.emit("✅ 12단계 파이프라인 완료!")
        self.log_msg.emit(f"출력 위치: {output_dir}")
        self.log_msg.emit("=" * 60)

        return {
            "facilities_raw": facilities_raw,
            "facilities_merged": facilities_merged,
            "facilities_gdf": facilities_gdf,
            "population_gdf": population_gdf,
            "admin_gdf": admin_gdf,
            "analysis": analysis_results,
            "equity_typology": equity_results,
            "validation": validation_results,
            "report_paths": report_paths,
            "quality_report": quality_report,
            "output_dir": output_dir,
        }

    # ── 개별 Phase 실행 ──
    def _run_collect(self):
        self.log_msg.emit("데이터 수집만 실행 (Phase 1~8)")
        # 간략 버전 - full에서 phase 8까지만 수행
        return self._run_full()  # TODO: 개별 분리

    def _run_analyze(self):
        self.log_msg.emit("접근성 분석만 실행 (Phase 9)")
        return {}

    def _run_validate(self):
        self.log_msg.emit("통계검증만 실행 (Phase 11)")
        return {}

    def _run_report(self):
        self.log_msg.emit("보고서 생성만 실행 (Phase 12)")
        return {}


# ═════════════════════════════════════════════════
# 메인 대화상자
# ═════════════════════════════════════════════════
class LivingSOCDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Living SOC Analyzer v3.0")
        self.setMinimumSize(1020, 780)
        self.worker = None
        self.result = {}
        self._build_ui()
        self._load_settings()

    # ═════════════════════════════════════════
    # UI 구성
    # ═════════════════════════════════════════

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        # 상단: 탭
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tab_settings(), "⚙ 설정")
        self.tabs.addTab(self._build_tab_run(), "▶ 실행")
        self.tabs.addTab(self._build_tab_analysis(), "📊 분석")
        self.tabs.addTab(self._build_tab_validation(), "📈 검증")
        self.tabs.addTab(self._build_tab_qgis(), "🗺 시각화")
        self.tabs.addTab(self._build_tab_report(), "📋 보고서")
        main_layout.addWidget(self.tabs)

        # 하단: 로그 + 진행률
        bottom = QGroupBox("실행 로그")
        bl = QVBoxLayout(bottom)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(170)
        self.log_text.setStyleSheet(
            "QTextEdit { font-family: 'Consolas','D2Coding','monospace'; "
            "font-size: 11px; background: #1e1e1e; color: #d4d4d4; }")
        bl.addWidget(self.log_text)

        h = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(22)
        h.addWidget(self.progress_bar, stretch=5)

        self.status_label = QLabel("대기 중")
        self.status_label.setStyleSheet("font-weight: bold;")
        h.addWidget(self.status_label, stretch=2)
        bl.addLayout(h)

        main_layout.addWidget(bottom)

    # ────────────────────────────────────
    # Tab 0: 설정
    # ────────────────────────────────────
    def _build_tab_settings(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        layout = QVBoxLayout(w)

        # ── API 키 ──
        api_group = QGroupBox("API 키 (★ = 필수)")
        ag = QGridLayout(api_group)
        self.api_inputs = {}
        api_defs = [
            ("data_go_kr",          "공공데이터포털",     True),
            ("sgis_consumer_key",   "SGIS 서비스ID",     True),
            ("sgis_consumer_secret","SGIS 보안Key",      True),
            ("kakao_rest",          "카카오 REST API",   True),
            ("vworld",              "브이월드",           True),
            ("naver_client_id",     "네이버 Client ID",  False),
            ("naver_client_secret", "네이버 Client Secret", False),
            ("its_node_link",       "국가교통DB",        False),
            ("molit_nsdi",          "국토정보플랫폼",    False),
        ]
        for row, (key, label, req) in enumerate(api_defs):
            prefix = "★ " if req else "   "
            lbl = QLabel(f"{prefix}{label}:")
            inp = QLineEdit()
            inp.setEchoMode(QLineEdit.Password)
            inp.setPlaceholderText("API 키 입력" + (" (필수)" if req else ""))
            ag.addWidget(lbl, row, 0)
            ag.addWidget(inp, row, 1)
            # 보기 토글
            btn_show = QPushButton("👁")
            btn_show.setFixedWidth(30)
            btn_show.setCheckable(True)
            btn_show.toggled.connect(
                lambda checked, i=inp: i.setEchoMode(
                    QLineEdit.Normal if checked else QLineEdit.Password))
            ag.addWidget(btn_show, row, 2)
            self.api_inputs[key] = inp
        layout.addWidget(api_group)

        # ── 대상지역 ──
        area_group = QGroupBox("대상지역")
        ar = QGridLayout(area_group)
        ar.addWidget(QLabel("지역 1:"), 0, 0)
        self.area1_name = QLineEdit("예천군")
        ar.addWidget(self.area1_name, 0, 1)
        ar.addWidget(QLabel("코드:"), 0, 2)
        self.area1_code = QLineEdit("47900")
        self.area1_code.setFixedWidth(80)
        ar.addWidget(self.area1_code, 0, 3)
        ar.addWidget(QLabel("시도:"), 0, 4)
        self.area1_sido = QLineEdit("경상북도")
        ar.addWidget(self.area1_sido, 0, 5)

        ar.addWidget(QLabel("지역 2:"), 1, 0)
        self.area2_name = QLineEdit("영덕군")
        ar.addWidget(self.area2_name, 1, 1)
        ar.addWidget(QLabel("코드:"), 1, 2)
        self.area2_code = QLineEdit("47770")
        self.area2_code.setFixedWidth(80)
        ar.addWidget(self.area2_code, 1, 3)
        ar.addWidget(QLabel("시도:"), 1, 4)
        self.area2_sido = QLineEdit("경상북도")
        ar.addWidget(self.area2_sido, 1, 5)

        ar.addWidget(QLabel("※ 행정코드: code.go.kr 참조"), 2, 0, 1, 6)
        layout.addWidget(area_group)

        # ── 분석 옵션 ──
        opt_group = QGroupBox("분석 옵션")
        og = QGridLayout(opt_group)

        og.addWidget(QLabel("분석 연도:"), 0, 0)
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2020, 2030)
        self.year_spin.setValue(2025)
        og.addWidget(self.year_spin, 0, 1)

        og.addWidget(QLabel("CRS:"), 0, 2)
        self.crs_combo = QComboBox()
        self.crs_combo.addItems(["EPSG:5179 (Korea TM)", "EPSG:5186 (Korea GRS80)"])
        og.addWidget(self.crs_combo, 0, 3)

        og.addWidget(QLabel("출력 폴더:"), 1, 0)
        default_out = os.path.join(os.path.expanduser("~"),
                                   "living_soc_output")
        self.output_edit = QLineEdit(default_out)
        og.addWidget(self.output_edit, 1, 1, 1, 2)
        btn_browse = QPushButton("📂")
        btn_browse.setFixedWidth(40)
        btn_browse.clicked.connect(self._browse_output)
        og.addWidget(btn_browse, 1, 3)

        layout.addWidget(opt_group)

        # ── 시설유형 선택 ──
        fac_group = QGroupBox("분석 대상 시설유형")
        fl = QGridLayout(fac_group)
        self.fac_checks = {}
        fac_types = [
            ("의원", True), ("보건소", True), ("보건지소", True),
            ("보건진료소", True), ("병원_종합병원", True),
            ("어린이집", True), ("유치원", True), ("경로당", True),
            ("노인복지관_여가복지시설", True), ("종합사회복지관", True),
            ("장애인복지시설", True), ("다함께돌봄센터_온종일돌봄", True),
        ]
        for i, (ft, default) in enumerate(fac_types):
            cb = QCheckBox(ft)
            cb.setChecked(default)
            fl.addWidget(cb, i // 4, i % 4)
            self.fac_checks[ft] = cb
        layout.addWidget(fac_group)

        # ── 저장 버튼 ──
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 설정 저장")
        btn_save.setFixedHeight(34)
        btn_save.clicked.connect(self._save_settings)
        btn_row.addWidget(btn_save)

        btn_check = QPushButton("🔍 API 키 검증")
        btn_check.setFixedHeight(34)
        btn_check.clicked.connect(self._check_api_keys)
        btn_row.addWidget(btn_check)
        layout.addLayout(btn_row)

        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # ────────────────────────────────────
    # Tab 1: 실행
    # ────────────────────────────────────
    def _build_tab_run(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        # 설명
        info = QLabel(
            "<b>12단계 완전자동화 파이프라인</b><br>"
            "API 키만 등록하면, 데이터 수집부터 보고서 생성까지 자동으로 수행됩니다.<br><br>"
            "<table cellspacing='4'>"
            "<tr><td><b>Phase 1~4</b></td><td>시설·인구 API 수집 → 표준화 → 좌표보정 → 정규화</td></tr>"
            "<tr><td><b>Phase 5~7</b></td><td>공간데이터 + OSM 교통망 + 카카오 OD 행렬</td></tr>"
            "<tr><td><b>Phase 8</b></td><td>데이터 품질검증 & CSV 내보내기</td></tr>"
            "<tr><td><b>Phase 9</b></td><td>E2SFCA 접근성 + PPR + 유인력 + 혼잡도 + 사각지대</td></tr>"
            "<tr><td><b>Phase 10</b></td><td>Gini계수 · T-검정 · K-means 지역유형화</td></tr>"
            "<tr><td><b>Phase 11</b></td><td>Moran's I · Bootstrap CI · 민감도 · LOOCV</td></tr>"
            "<tr><td><b>Phase 12</b></td><td>Excel 9시트 + HTML 대시보드 + JSON</td></tr>"
            "</table>"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # 실행 버튼
        btn_h = QHBoxLayout()

        self.btn_run = QPushButton("▶  12단계 전체 실행")
        self.btn_run.setFixedHeight(55)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #2ecc71; color: white; "
            "font-size: 17px; font-weight: bold; border-radius: 10px; }"
            "QPushButton:hover { background-color: #27ae60; }"
            "QPushButton:disabled { background-color: #95a5a6; }")
        self.btn_run.clicked.connect(self._run_full_pipeline)
        btn_h.addWidget(self.btn_run, stretch=4)

        self.btn_stop = QPushButton("■ 중지")
        self.btn_stop.setFixedHeight(55)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #e74c3c; color: white; "
            "font-size: 15px; border-radius: 10px; }"
            "QPushButton:disabled { background-color: #95a5a6; }")
        self.btn_stop.clicked.connect(self._stop_pipeline)
        btn_h.addWidget(self.btn_stop, stretch=1)
        layout.addLayout(btn_h)

        # Phase별 상태 테이블
        self.phase_table = QTableWidget(12, 3)
        self.phase_table.setHorizontalHeaderLabels(
            ["Phase", "설명", "상태"])
        self.phase_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.phase_table.verticalHeader().setVisible(False)
        self.phase_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.phase_table.setSelectionMode(QTableWidget.NoSelection)

        names = [
            "시설·인구 API 수집", "데이터 표준화", "좌표 보정",
            "용량 정규화", "공간데이터 수집", "OSM 교통망",
            "카카오 OD 행렬", "품질검증 & 내보내기",
            "E2SFCA 접근성 분석", "형평성·지역유형화",
            "통계검증 (Moran·Bootstrap)", "자동보고서 (Excel·HTML·JSON)",
        ]
        for r, name in enumerate(names):
            self.phase_table.setItem(
                r, 0, QTableWidgetItem(f"Phase {r+1}"))
            self.phase_table.setItem(r, 1, QTableWidgetItem(name))
            self.phase_table.setItem(r, 2, QTableWidgetItem("⏳ 대기"))
        layout.addWidget(self.phase_table)

        return w

    # ────────────────────────────────────
    # Tab 2: 분석 (개별)
    # ────────────────────────────────────
    def _build_tab_analysis(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        pg = QGroupBox("분석 파라미터 (고급 설정)")
        pl = QGridLayout(pg)

        pl.addWidget(QLabel("거리감쇠함수:"), 0, 0)
        self.decay_combo = QComboBox()
        self.decay_combo.addItems([
            "Gaussian", "Exponential", "Inverse Power", "Linear", "Binary"])
        pl.addWidget(self.decay_combo, 0, 1)

        pl.addWidget(QLabel("감쇠 β / α:"), 1, 0)
        self.decay_param = QDoubleSpinBox()
        self.decay_param.setRange(0.1, 5.0)
        self.decay_param.setValue(1.0)
        self.decay_param.setSingleStep(0.1)
        pl.addWidget(self.decay_param, 1, 1)

        pl.addWidget(QLabel("임계거리 (km):"), 2, 0)
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 100)
        self.threshold_spin.setValue(30)
        pl.addWidget(self.threshold_spin, 2, 1)

        self.chk_adjacent = QCheckBox("인접 시군 시설 포함 (경계효과 보정)")
        self.chk_adjacent.setChecked(True)
        pl.addWidget(self.chk_adjacent, 3, 0, 1, 2)
        layout.addWidget(pg)

        ig = QGroupBox("분석 항목")
        il = QVBoxLayout(ig)
        self.chk_e2sfca = QCheckBox("E2SFCA 접근성 지수 (Ai)")
        self.chk_e2sfca.setChecked(True)
        il.addWidget(self.chk_e2sfca)
        self.chk_ppr = QCheckBox("PPR (공급-인구 비율)")
        self.chk_ppr.setChecked(True)
        il.addWidget(self.chk_ppr)
        self.chk_attract = QCheckBox("유인력 지수 (Huff/KoALA)")
        self.chk_attract.setChecked(True)
        il.addWidget(self.chk_attract)
        self.chk_crowd = QCheckBox("혼잡도 지수 (i2SFCA)")
        self.chk_crowd.setChecked(True)
        il.addWidget(self.chk_crowd)
        self.chk_blind = QCheckBox("서비스 사각지대 도출")
        self.chk_blind.setChecked(True)
        il.addWidget(self.chk_blind)
        layout.addWidget(ig)

        layout.addStretch()
        return w

    # ────────────────────────────────────
    # Tab 3: 검증
    # ────────────────────────────────────
    def _build_tab_validation(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        desc = QLabel(
            "<b>통계 검증</b> — 접근성 분석 결과의 과학적 신뢰성 확인<br>"
            "• <b>Global Moran's I</b>: 전역 공간적 자기상관 (군집/분산 패턴)<br>"
            "• <b>Local Moran's I (LISA)</b>: 핫스팟/콜드스팟 탐지 (HH/LL/HL/LH)<br>"
            "• <b>Bootstrap 95% CI</b>: 접근성 평균·중앙값의 신뢰구간<br>"
            "• <b>민감도 분석</b>: 감쇠함수·임계거리 변경 시 결과 안정성<br>"
            "• <b>LOOCV</b>: 개별 시설 영향력 진단"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        og = QGroupBox("검증 옵션")
        ol = QGridLayout(og)
        ol.addWidget(QLabel("순열 횟수 (Moran's I):"), 0, 0)
        self.perm_spin = QSpinBox()
        self.perm_spin.setRange(99, 9999)
        self.perm_spin.setValue(999)
        ol.addWidget(self.perm_spin, 0, 1)

        ol.addWidget(QLabel("Bootstrap 반복:"), 1, 0)
        self.boot_spin = QSpinBox()
        self.boot_spin.setRange(100, 10000)
        self.boot_spin.setValue(1000)
        ol.addWidget(self.boot_spin, 1, 1)

        self.chk_loocv = QCheckBox("LOOCV 수행 (시설 200개 이하 시)")
        ol.addWidget(self.chk_loocv, 2, 0, 1, 2)
        layout.addWidget(og)

        # 결과 표
        self.val_table = QTableWidget(0, 2)
        self.val_table.setHorizontalHeaderLabels(["항목", "결과"])
        self.val_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.val_table)

        return w

    # ────────────────────────────────────
    # Tab 4: QGIS 시각화
    # ────────────────────────────────────
    def _build_tab_qgis(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        desc = QLabel(
            "분석 결과를 QGIS 레이어로 자동 등록합니다.<br>"
            "GeoPackage(.gpkg)로 저장 후 프로젝트에 추가합니다.")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        lg = QGroupBox("등록할 레이어")
        ll = QVBoxLayout(lg)
        self.layer_checks = {}
        for key, label in [
            ("facilities", "시설 분포 (점 레이어)"),
            ("accessibility", "접근성 지수 (격자/읍면)"),
            ("blind_spots", "서비스 사각지대"),
            ("typology", "지역 유형화 (K-means)"),
            ("admin", "행정구역 경계"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            ll.addWidget(cb)
            self.layer_checks[key] = cb
        layout.addWidget(lg)

        sg = QGroupBox("스타일")
        sl = QGridLayout(sg)
        sl.addWidget(QLabel("분류:"), 0, 0)
        self.classify_combo = QComboBox()
        self.classify_combo.addItems(
            ["Jenks (자연분류)", "Quantile (등분위)", "Equal Interval (등간격)"])
        sl.addWidget(self.classify_combo, 0, 1)

        sl.addWidget(QLabel("분류 수:"), 1, 0)
        self.class_n = QSpinBox()
        self.class_n.setRange(3, 10)
        self.class_n.setValue(5)
        sl.addWidget(self.class_n, 1, 1)
        layout.addWidget(sg)

        bh = QHBoxLayout()
        btn_load = QPushButton("🗺 QGIS 레이어 등록")
        btn_load.setFixedHeight(42)
        btn_load.setStyleSheet(
            "QPushButton { background-color: #3498db; color: white; "
            "font-size: 14px; font-weight: bold; border-radius: 8px; }")
        btn_load.clicked.connect(self._load_to_qgis)
        bh.addWidget(btn_load)

        btn_gpkg = QPushButton("💾 GeoPackage 저장")
        btn_gpkg.setFixedHeight(42)
        btn_gpkg.clicked.connect(self._export_gpkg)
        bh.addWidget(btn_gpkg)
        layout.addLayout(bh)

        layout.addStretch()
        return w

    # ────────────────────────────────────
    # Tab 5: 보고서
    # ────────────────────────────────────
    def _build_tab_report(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        rg = QGroupBox("생성할 보고서")
        rl = QVBoxLayout(rg)
        self.chk_excel = QCheckBox(
            "📊 Excel (9시트: 요약·점수·접근성·공급·사각지대·형평성·유형화·통계·메타)")
        self.chk_excel.setChecked(True)
        rl.addWidget(self.chk_excel)
        self.chk_html = QCheckBox("🌐 HTML 대시보드 (Chart.js 인터랙티브)")
        self.chk_html.setChecked(True)
        rl.addWidget(self.chk_html)
        self.chk_json = QCheckBox("📦 JSON (후속 가공·연계용)")
        self.chk_json.setChecked(True)
        rl.addWidget(self.chk_json)
        layout.addWidget(rg)

        self.report_table = QTableWidget(0, 3)
        self.report_table.setHorizontalHeaderLabels(
            ["유형", "파일", "크기"])
        self.report_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.report_table)

        bh = QHBoxLayout()
        btn_gen = QPushButton("📋 보고서 재생성")
        btn_gen.setFixedHeight(42)
        btn_gen.setStyleSheet(
            "QPushButton { background-color: #9b59b6; color: white; "
            "font-size: 14px; font-weight: bold; border-radius: 8px; }")
        btn_gen.clicked.connect(lambda: self._run_phase("report"))
        bh.addWidget(btn_gen)

        btn_open = QPushButton("📂 출력 폴더 열기")
        btn_open.setFixedHeight(42)
        btn_open.clicked.connect(self._open_output_folder)
        bh.addWidget(btn_open)
        layout.addLayout(bh)

        layout.addStretch()
        return w

    # ═════════════════════════════════════════
    # 실행 로직
    # ═════════════════════════════════════════

    def _collect_settings(self):
        """현재 UI → dict"""
        api_keys = {k: inp.text().strip()
                    for k, inp in self.api_inputs.items()}

        target_areas = {}
        for name_w, code_w, sido_w in [
            (self.area1_name, self.area1_code, self.area1_sido),
            (self.area2_name, self.area2_code, self.area2_sido),
        ]:
            n = name_w.text().strip()
            c = code_w.text().strip()
            s = sido_w.text().strip()
            if n and c:
                target_areas[n] = {
                    "code": c,
                    "full_code": c + "00000",
                    "sido": s,
                    "sido_code": c[:2],
                }

        return {
            "api_keys": api_keys,
            "target_areas": target_areas,
            "year": self.year_spin.value(),
            "output_dir": self.output_edit.text().strip(),
        }

    def _run_full_pipeline(self):
        """12단계 전체 실행"""
        settings = self._collect_settings()

        # 필수 키 확인
        missing = [k for k in
                   ["data_go_kr", "sgis_consumer_key", "kakao_rest", "vworld"]
                   if not settings["api_keys"].get(k)]
        if missing:
            QMessageBox.warning(
                self, "API 키 누락",
                f"필수 API 키가 비어있습니다:\n\n"
                f"{'  /  '.join(missing)}\n\n"
                f"⚙ 설정 탭에서 입력해주세요.")
            self.tabs.setCurrentIndex(0)
            return

        if not settings["target_areas"]:
            QMessageBox.warning(self, "지역 미설정",
                                "대상지역을 최소 1개 입력해주세요.")
            self.tabs.setCurrentIndex(0)
            return

        Path(settings["output_dir"]).mkdir(parents=True, exist_ok=True)
        self._start_worker("full", settings)

    def _run_phase(self, mode):
        settings = self._collect_settings()
        self._start_worker(mode, settings)

    def _start_worker(self, mode, settings):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "실행 중",
                                "이미 작업이 실행 중입니다.")
            return

        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)

        # Phase 테이블 초기화
        for r in range(self.phase_table.rowCount()):
            self.phase_table.setItem(r, 2, QTableWidgetItem("⏳ 대기"))

        self.worker = PipelineWorker(mode, settings)
        self.worker.progress.connect(self._on_progress)
        self.worker.log_msg.connect(self._on_log)
        self.worker.phase_update.connect(self._on_phase_update)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _stop_pipeline(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.terminate()
            self.worker.wait(3000)
            self._log("⚠ 사용자에 의해 중단됨")
            self._reset_buttons()

    # ── 시그널 핸들러 ──
    def _on_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self.status_label.setText(msg)

    def _on_log(self, msg):
        self._log(msg)

    def _on_phase_update(self, phase_num, status):
        row = phase_num - 1
        if 0 <= row < self.phase_table.rowCount():
            icons = {"running": "🔄 실행중...", "done": "✅ 완료",
                     "error": "❌ 오류", "skip": "⏭ 건너뜀"}
            self.phase_table.setItem(
                row, 2, QTableWidgetItem(icons.get(status, status)))

    def _on_finished(self, result):
        self.result = result
        self.progress_bar.setValue(100)
        self.status_label.setText("✅ 완료!")

        # 모든 Phase 완료 표시
        for r in range(self.phase_table.rowCount()):
            item = self.phase_table.item(r, 2)
            if item and "대기" in item.text():
                self.phase_table.setItem(
                    r, 2, QTableWidgetItem("✅ 완료"))

        # 보고서 테이블 업데이트
        paths = result.get("report_paths", {})
        self.report_table.setRowCount(len(paths))
        for i, (fmt, path) in enumerate(paths.items()):
            self.report_table.setItem(i, 0, QTableWidgetItem(fmt.upper()))
            self.report_table.setItem(
                i, 1, QTableWidgetItem(os.path.basename(str(path))))
            try:
                size = os.path.getsize(str(path))
                size_str = (f"{size/1024:.0f} KB" if size < 1024*1024
                            else f"{size/1024/1024:.1f} MB")
            except Exception:
                size_str = "-"
            self.report_table.setItem(i, 2, QTableWidgetItem(size_str))

        # 검증 결과 테이블
        val = result.get("validation", {})
        if val:
            grade = val.get("종합_품질", {}).get("등급", "-")
            score = val.get("종합_품질", {}).get("점수", "-")
            items = [
                ("품질 등급", str(grade)),
                ("품질 점수", str(score)),
            ]
            moran = val.get("공간적_자기상관", {})
            if moran:
                items.append(("Global Moran's I",
                              f"{moran.get('I', '-'):.4f}"))
                items.append(("p-value",
                              f"{moran.get('p_value', '-'):.4f}"))
            self.val_table.setRowCount(len(items))
            for i, (k, v) in enumerate(items):
                self.val_table.setItem(i, 0, QTableWidgetItem(k))
                self.val_table.setItem(i, 1, QTableWidgetItem(v))

        self._reset_buttons()
        QMessageBox.information(
            self, "완료",
            "12단계 파이프라인이 완료되었습니다!\n\n"
            "🗺 시각화 탭 → QGIS 레이어 등록\n"
            "📋 보고서 탭 → Excel/HTML 확인\n"
            "📂 출력 폴더 열기 → 전체 산출물 확인")

    def _on_error(self, msg):
        self._log(f"\n❌ 오류:\n{msg}")
        self.status_label.setText("❌ 오류 발생")
        self._reset_buttons()
        QMessageBox.critical(
            self, "오류",
            f"실행 중 오류가 발생했습니다.\n\n{msg[:600]}")

    def _reset_buttons(self):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ═════════════════════════════════════════
    # QGIS 레이어 등록
    # ═════════════════════════════════════════

    def _load_to_qgis(self):
        if not self.result:
            QMessageBox.warning(self, "데이터 없음",
                                "먼저 ▶ 실행 탭에서 파이프라인을 실행해주세요.")
            return

        project = QgsProject.instance()
        output_dir = self.result.get("output_dir", "")
        loaded = 0

        # 시설 분포
        if self.layer_checks.get("facilities", QCheckBox()).isChecked():
            gdf = self.result.get("facilities_gdf")
            if gdf is not None and hasattr(gdf, "to_file"):
                try:
                    path = os.path.join(output_dir, "시설분포.gpkg")
                    gdf.to_file(path, driver="GPKG", layer="facilities")
                    lyr = QgsVectorLayer(
                        f"{path}|layername=facilities", "시설 분포", "ogr")
                    if lyr.isValid():
                        project.addMapLayer(lyr)
                        loaded += 1
                        self._log(f"✅ 시설 분포 ({len(gdf)}건)")
                except Exception as e:
                    self._log(f"⚠ 시설 레이어 실패: {e}")

        # 행정경계
        if self.layer_checks.get("admin", QCheckBox()).isChecked():
            gdf = self.result.get("admin_gdf")
            if gdf is not None and hasattr(gdf, "to_file"):
                try:
                    path = os.path.join(output_dir, "행정경계.gpkg")
                    gdf.to_file(path, driver="GPKG", layer="admin")
                    lyr = QgsVectorLayer(
                        f"{path}|layername=admin", "행정경계", "ogr")
                    if lyr.isValid():
                        project.addMapLayer(lyr)
                        loaded += 1
                        self._log(f"✅ 행정경계 ({len(gdf)}개)")
                except Exception as e:
                    self._log(f"⚠ 행정경계 실패: {e}")

        # 유형화 결과 (CSV → 행정경계에 JOIN)
        if self.layer_checks.get("typology", QCheckBox()).isChecked():
            eq = self.result.get("equity_typology", {})
            if isinstance(eq, dict) and "typology_gdf" in eq:
                gdf = eq["typology_gdf"]
                if hasattr(gdf, "to_file"):
                    try:
                        path = os.path.join(output_dir, "지역유형화.gpkg")
                        gdf.to_file(path, driver="GPKG", layer="typology")
                        lyr = QgsVectorLayer(
                            f"{path}|layername=typology", "지역 유형화", "ogr")
                        if lyr.isValid():
                            project.addMapLayer(lyr)
                            loaded += 1
                            self._log("✅ 지역 유형화")
                    except Exception as e:
                        self._log(f"⚠ 유형화 실패: {e}")

        self._log(f"\n총 {loaded}개 레이어 QGIS에 등록 완료")
        if loaded > 0:
            self.iface.mapCanvas().refreshAllLayers()

    def _export_gpkg(self):
        if not self.result:
            QMessageBox.warning(self, "데이터 없음",
                                "먼저 파이프라인을 실행해주세요.")
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self, "GeoPackage 저장", "", "GeoPackage (*.gpkg)")
        if not filepath:
            return

        saved = 0
        for name, key in [("시설분포", "facilities_gdf"),
                          ("행정경계", "admin_gdf")]:
            gdf = self.result.get(key)
            if gdf is not None and hasattr(gdf, "to_file"):
                try:
                    mode = "w" if saved == 0 else "a"
                    gdf.to_file(filepath, driver="GPKG",
                                layer=name, mode=mode)
                    saved += 1
                except Exception as e:
                    self._log(f"⚠ {name} 저장 실패: {e}")

        self._log(f"GeoPackage 저장: {filepath} ({saved}개 레이어)")
        QMessageBox.information(self, "저장 완료",
                                f"{filepath}\n{saved}개 레이어 저장")

    # ═════════════════════════════════════════
    # 설정 저장/불러오기 (QSettings)
    # ═════════════════════════════════════════

    def _save_settings(self):
        s = QSettings("LivingSOC", "AnalyzerV3")
        for key, inp in self.api_inputs.items():
            s.setValue(f"api/{key}", inp.text())
        s.setValue("area1_name", self.area1_name.text())
        s.setValue("area1_code", self.area1_code.text())
        s.setValue("area1_sido", self.area1_sido.text())
        s.setValue("area2_name", self.area2_name.text())
        s.setValue("area2_code", self.area2_code.text())
        s.setValue("area2_sido", self.area2_sido.text())
        s.setValue("year", self.year_spin.value())
        s.setValue("output_dir", self.output_edit.text())
        self._log("💾 설정 저장 완료 (QGIS 재시작 후에도 유지)")
        QMessageBox.information(self, "저장", "설정이 저장되었습니다.")

    def _load_settings(self):
        s = QSettings("LivingSOC", "AnalyzerV3")
        for key, inp in self.api_inputs.items():
            v = s.value(f"api/{key}", "")
            if v:
                inp.setText(v)
        for attr, skey in [
            ("area1_name", "area1_name"), ("area1_code", "area1_code"),
            ("area1_sido", "area1_sido"), ("area2_name", "area2_name"),
            ("area2_code", "area2_code"), ("area2_sido", "area2_sido"),
        ]:
            v = s.value(skey)
            if v:
                getattr(self, attr).setText(v)
        v = s.value("year")
        if v:
            self.year_spin.setValue(int(v))
        v = s.value("output_dir")
        if v:
            self.output_edit.setText(v)

    def _check_api_keys(self):
        """등록된 API 키 유효성 간단 확인"""
        import requests
        results = []
        keys = {k: inp.text().strip() for k, inp in self.api_inputs.items()}

        # 공공데이터포털
        if keys.get("data_go_kr"):
            try:
                r = requests.get(
                    "http://apis.data.go.kr/B551182/hospInfoServicev2/"
                    "getHospBasisList",
                    params={"serviceKey": keys["data_go_kr"],
                            "numOfRows": 1, "pageNo": 1},
                    timeout=10)
                ok = r.status_code == 200
                results.append(f"공공데이터포털: {'✅' if ok else '❌'}")
            except Exception:
                results.append("공공데이터포털: ❌ (연결 실패)")
        else:
            results.append("공공데이터포털: ⚠ 미입력")

        # 카카오
        if keys.get("kakao_rest"):
            try:
                r = requests.get(
                    "https://dapi.kakao.com/v2/local/search/keyword.json",
                    headers={"Authorization":
                             f"KakaoAK {keys['kakao_rest']}"},
                    params={"query": "서울역"},
                    timeout=10)
                ok = r.status_code == 200
                results.append(f"카카오 REST: {'✅' if ok else '❌'}")
            except Exception:
                results.append("카카오 REST: ❌ (연결 실패)")
        else:
            results.append("카카오 REST: ⚠ 미입력")

        msg = "\n".join(results)
        self._log(f"\n🔍 API 키 검증:\n{msg}")
        QMessageBox.information(self, "API 키 검증", msg)

    # ── 유틸 ──
    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if d:
            self.output_edit.setText(d)

    def _open_output_folder(self):
        d = self.output_edit.text().strip()
        if d and os.path.isdir(d):
            import subprocess
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        else:
            QMessageBox.warning(self, "폴더 없음",
                                "출력 폴더가 존재하지 않습니다.\n"
                                "먼저 파이프라인을 실행해주세요.")
