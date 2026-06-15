"""
统一离线报告格式模板
提供标准化的报告格式模板，确保所有报告输出格式一致
"""

from datetime import datetime
from typing import Dict, Any

# =============================================================================
# 报告格式模板
# =============================================================================

class ReportTemplate:
    """报告格式模板类"""
    
    # 标题模板
    TITLE_TEMPLATE = "# 📊 StockAI 量化分析报告 —— {ts_code} {name}"
    
    # 基本信息模板
    HEADER_TEMPLATE = """
**分析时间**：{analysis_time}
**股票代码**：{ts_code}
**股票名称**：{name}
**所属行业**：{industry}
"""
    
    # 评分摘要模板
    SCORE_SUMMARY_TEMPLATE = """
## 🎯 综合评分摘要

| 评估维度 | 得分 | 说明 |
|:---|---:|:---|
| Python量化评分 | {python_score}/70 | 基于技术面、资金面、基本面多维度量化分析 |
| AI智能评分 | {ai_score}/30 | 基于产业催化、环境适配的智能评估 |
| **综合得分** | **{total_score}/100** | **{grade}级** |

**总分计算公式**：Python评分({python_score}) + AI评分({ai_score}) = 最终得分 **{total_score}**分{downgrade_reason}
"""
    
    # 风险提示模板
    RISK_WARNING_TEMPLATE = """
## ⚠️ 风险提示

{risk_content}

**免责声明**：本报告由 StockAI 量化分析系统自动生成，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。
"""
    
    # 报告页脚模板
    FOOTER_TEMPLATE = """
---
**报告生成时间**：{generation_time}
**数据来源**：Tushare 实时行情 | 仅供参考
**StockAI 版本**：v4.0
"""

    @staticmethod
    def generate_title(ts_code: str, name: str) -> str:
        """生成报告标题"""
        return ReportTemplate.TITLE_TEMPLATE.format(
            ts_code=ts_code,
            name=name
        )
    
    @staticmethod
    def generate_header(ts_code: str, name: str, industry: str = "") -> str:
        """生成报告头部信息"""
        return ReportTemplate.HEADER_TEMPLATE.format(
            analysis_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ts_code=ts_code,
            name=name,
            industry=industry or "未知"
        )
    
    @staticmethod
    def generate_score_summary(python_score: float, ai_score: float, 
                               total_score: float, grade: str, 
                               downgrade_reason: str = "") -> str:
        """生成评分摘要"""
        return ReportTemplate.SCORE_SUMMARY_TEMPLATE.format(
            python_score=int(python_score),
            ai_score=int(ai_score),
            total_score=int(total_score),
            grade=grade,
            downgrade_reason=downgrade_reason or ""
        )
    
    @staticmethod
    def generate_risk_warning(risk_content: str) -> str:
        """生成风险提示"""
        return ReportTemplate.RISK_WARNING_TEMPLATE.format(
            risk_content=risk_content
        )
    
    @staticmethod
    def generate_footer() -> str:
        """生成报告页脚"""
        return ReportTemplate.FOOTER_TEMPLATE.format(
            generation_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    
    @staticmethod
    def format_complete_report(ts_code: str, name: str, industry: str,
                              python_score: float, ai_score: float,
                              total_score: float, grade: str,
                              ai_analysis: str, risk_content: str = "",
                              downgrade_reason: str = "") -> str:
        """
        生成完整格式的报告
        
        Args:
            ts_code: 股票代码
            name: 股票名称
            industry: 所属行业
            python_score: Python评分
            ai_score: AI评分
            total_score: 总分
            grade: 评级
            ai_analysis: AI分析内容
            risk_content: 风险提示内容
            downgrade_reason: 降级原因
        
        Returns:
            完整格式的报告文本
        """
        sections = []
        
        # 标题
        sections.append(ReportTemplate.generate_title(ts_code, name))
        
        # 头部信息
        sections.append(ReportTemplate.generate_header(ts_code, name, industry))
        
        # 评分摘要
        sections.append(ReportTemplate.generate_score_summary(
            python_score, ai_score, total_score, grade, downgrade_reason
        ))
        
        # AI分析内容
        if ai_analysis:
            sections.append(ai_analysis)
        
        # 风险提示
        if risk_content:
            sections.append(ReportTemplate.generate_risk_warning(risk_content))
        else:
            sections.append(ReportTemplate.generate_risk_warning("暂无特殊风险提示"))
        
        # 页脚
        sections.append(ReportTemplate.generate_footer())
        
        return "\n".join(sections)


# =============================================================================
# 报告格式验证
# =============================================================================

class ReportValidator:
    """报告格式验证器"""
    
    @staticmethod
    def validate_report_structure(report_md: str) -> Dict[str, Any]:
        """
        验证报告结构完整性
        
        Args:
            report_md: 报告Markdown文本
        
        Returns:
            验证结果字典
        """
        result = {
            "is_valid": True,
            "errors": [],
            "warnings": []
        }
        
        # 检查必需的章节
        required_sections = [
            "📊",  # 标题标识
            "综合评分",  # 评分摘要
            "风险提示"  # 风险提示
        ]
        
        for section in required_sections:
            if section not in report_md:
                result["errors"].append(f"缺少必需章节: {section}")
                result["is_valid"] = False
        
        # 检查评分格式
        if "综合得分" in report_md:
            if "**" not in report_md.split("综合得分")[1].split("\n")[0]:
                result["warnings"].append("综合得分未使用粗体格式")
        
        # 检查时间戳
        if "分析时间" not in report_md:
            result["warnings"].append("缺少分析时间戳")
        
        return result
    
    @staticmethod
    def sanitize_report_content(report_md: str) -> str:
        """
        清理报告内容，确保格式统一
        
        Args:
            report_md: 原始报告文本
        
        Returns:
            清理后的报告文本
        """
        # 统一标题格式
        lines = report_md.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # 确保标题前后有空行
            if line.startswith('#'):
                if cleaned_lines and cleaned_lines[-1].strip():
                    cleaned_lines.append('')
            
            cleaned_lines.append(line)
        
        # 移除多余的空行
        final_lines = []
        prev_empty = False
        for line in cleaned_lines:
            is_empty = not line.strip()
            if not (is_empty and prev_empty):
                final_lines.append(line)
            prev_empty = is_empty
        
        return '\n'.join(final_lines)


# =============================================================================
# 报告格式工具函数
# =============================================================================

def format_grade_badge(grade: str) -> str:
    """格式化评级徽章"""
    grade_colors = {
        "S": "🔴",
        "A": "🟠", 
        "B": "🔵",
        "C": "⚪"
    }
    emoji = grade_colors.get(grade, "⚪")
    return f"{emoji} {grade}级"


def format_score_display(score: float, max_score: float = 100) -> str:
    """格式化分数显示"""
    percentage = (score / max_score) * 100
    return f"**{int(score)}**/{int(max_score)} ({percentage:.1f}%)"


def format_change(value: float, is_percentage: bool = False) -> str:
    """格式化变化值显示"""
    if value == 0:
        return "0"
    
    sign = "+" if value > 0 else ""
    unit = "%" if is_percentage else ""
    
    if value > 0:
        return f"<font color='green'>{sign}{value:.2f}{unit}</font>"
    else:
        return f"<font color='red'>{value:.2f}{unit}</font>"
