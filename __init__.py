"""
Living SOC Analyzer - QGIS Plugin v3.0
생활SOC 접근성·공급적합성 자동화 분석 (12단계 파이프라인)
"""


def classFactory(iface):
    from .living_soc_plugin import LivingSOCPlugin
    return LivingSOCPlugin(iface)
