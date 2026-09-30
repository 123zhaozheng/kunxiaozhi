"""Built-in scenario templates for bank staff (one-click start)."""

from __future__ import annotations

from src.kernel.schemas.scheduled_task import ScheduledTaskTemplate, ScheduleKind, ScheduleSpec

TEMPLATES: list[ScheduledTaskTemplate] = [
    ScheduledTaskTemplate(
        id="morning_brief",
        title="晨间工作简报",
        description="每个工作日上班前，帮我梳理今天的工作重点和注意事项",
        icon="sunrise",
        name="晨间工作简报",
        prompt="请以银行一线员工的视角，为我整理今天的工作简报：\n1. 今日需重点关注的工作事项与提醒；\n2. 近期常见的客户咨询问题与标准话术要点；\n3. 一句简短的合规与风险提示。\n要求：条理清晰、分点列出、控制在 500 字以内。",
        schedule=ScheduleSpec(kind=ScheduleKind.WEEKDAYS, time="08:30"),
    ),
    ScheduledTaskTemplate(
        id="weekly_report",
        title="工作周报初稿",
        description="每周五下午自动生成一份周报框架，改一改就能交",
        icon="file-text",
        name="本周工作周报",
        prompt="请帮我生成一份银行员工工作周报初稿，结构包括：本周完成工作、重点业务进展、遇到的问题及解决办法、下周工作计划。每部分给出可直接填写的要点提示，语言正式简洁。",
        schedule=ScheduleSpec(kind=ScheduleKind.WEEKLY, time="16:30", weekdays=[5]),
    ),
    ScheduledTaskTemplate(
        id="monthly_summary",
        title="月度总结提醒",
        description="每月最后一天提醒并帮我起草月度工作总结",
        icon="calendar",
        name="月度工作总结",
        prompt="今天是本月最后一天，请帮我起草月度工作总结的框架：本月主要业绩与亮点、客户服务情况、风险与合规情况、个人成长与不足、下月目标。每项给出填写提示。",
        schedule=ScheduleSpec(kind=ScheduleKind.MONTHLY, time="15:00", month_day=-1),
    ),
    ScheduledTaskTemplate(
        id="compliance_tip",
        title="每日合规小课堂",
        description="每天推送一条通俗易懂的合规与反诈知识",
        icon="shield",
        name="每日合规小课堂",
        prompt="请给我讲一条银行业务中常见的合规或反电信诈骗知识点：用一个真实感的小案例说明，最后总结 3 条可执行的注意事项。通俗易懂，300 字以内。",
        schedule=ScheduleSpec(kind=ScheduleKind.WEEKDAYS, time="09:00"),
    ),
    ScheduledTaskTemplate(
        id="learning_checkin",
        title="业务学习打卡",
        description="每周一、三、五学习一个金融业务知识点",
        icon="book-open",
        name="业务学习打卡",
        prompt="请为我讲解一个银行/金融业务知识点（如理财产品风险等级、个人贷款流程、外汇基础等，每次换一个），包含概念解释、实际场景示例和 3 道自测小题（附答案）。",
        schedule=ScheduleSpec(kind=ScheduleKind.WEEKLY, time="12:30", weekdays=[1, 3, 5]),
    ),
    ScheduledTaskTemplate(
        id="one_time_reminder",
        title="一次性提醒",
        description="在指定时间做一次，例如会议前帮我准备材料要点",
        icon="bell",
        name="一次性提醒",
        prompt="请提醒我：",
        schedule=ScheduleSpec(kind=ScheduleKind.ONCE),
    ),
]


def list_templates() -> list[ScheduledTaskTemplate]:
    return list(TEMPLATES)
