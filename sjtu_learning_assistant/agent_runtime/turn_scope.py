"""每轮检索来源和短追问；只继承用户原文，不从助手回复猜姓名。"""
from __future__ import annotations
from dataclasses import dataclass
import re

WEB = re.compile(r'在线(?:搜索|查询|检索|查|搜)|^在线$|联网|网上|互联网|网络搜索|网络查询|web\s+search|online\s+search|duckduckgo|bing', re.I)
LOCAL = re.compile(r'本地|邮箱|邮件|资料库|工作区|附件')
LOCAL_ONLY = re.compile(r'仅本地|只(?:查|读|用|使用)?本地|不要联网|不用联网|不联网|禁止联网')
RETRY = {'再查查', '再查一下', '继续查', '重新查', '再试试', '再搜一下', '再搜索一下', '还是不对'}


@dataclass(frozen=True)
class TurnScope:
    kind: str = 'default'
    query: str = ''
    brief: bool = False

    @property
    def web_only(self):
        return self.kind == 'web'

    @property
    def exact_subject(self):
        match = re.fullmatch(r'([\u4e00-\u9fff]{2,4})(?:是谁|是誰)', self.query)
        return match.group(1) if match else None


def _clean(text):
    return text.strip().rstrip('。！!？?')


def _source_command(text):
    rest = WEB.sub('', text)
    rest = re.sub(r'请|帮我|给我|改为|改用|使用|只用|仅用|只|仅|从|去|用|搜索|查询|查一下|查找|查|搜|一下|公开|网页|信息|[\s，,：:。！!?？]', '', rest)
    return not rest


def resolve_turn_scope(messages, user_text: str) -> TurnScope:
    text = _clean(user_text)
    previous = [m['content'] for m in messages if m.get('role') == 'user' and isinstance(m.get('content'), str)]
    if previous and _clean(previous[-1]) == text:
        previous.pop()
    previous = previous[-12:]
    query = text
    web = bool(WEB.search(text))
    local_only = bool(LOCAL_ONLY.search(text))
    local_text = re.sub(r'(?:不要|不用|不查|别查|禁止|无需|不必).{0,4}(?:本地|邮件|邮箱|资料库|工作区|附件)', '', text)
    local_action = re.search(r'同时|也查|并查|一起|结合|对照|根据附件|(?:写入|保存|输出|生成|整理).{0,8}(?:本地|工作区|文件)|(?:本地|工作区).{0,8}(?:写入|保存|输出|编辑)', local_text)
    mixed = web and bool(LOCAL.search(local_text)) and bool(local_action)
    source_only = web and _source_command(text)
    retry = text in RETRY
    if source_only or retry:
        for value in reversed(previous):
            value = _clean(value)
            if WEB.search(value):
                web = True
            if value in RETRY or _source_command(value):
                continue
            query = value
            break
        else:
            query = '' if source_only else text
    person = bool(re.fullmatch(r'(?!我|你|他|她|这|那|谁|课程|老师|助教|教授|同学|助理|课件|文件)(?:[\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z .\x27-]{1,79})(?:是谁|是誰)', query))
    if person and not re.search(r'本地|邮箱|邮件|资料库|工作区|附件|课程|课件|作业|联系人', query):
        web = True
    kind = 'local' if local_only else 'default' if mixed else 'web' if web else 'default'
    brief = kind == 'web' and len(query) <= 160 and not re.search(r'详细|深入|报告|对比|分析|综述|所有|全面', query)
    return TurnScope(kind, query, brief)


WEB_GUIDANCE = (
    '本轮只检索公开网页。继承的查询对象仅来自用户原文；不读取本地邮件、课程、工作区、日历或技能脚本，'
    '不把旧回复中的私人资料放进搜索关键词或最终回答。完整姓名/专名原样保留，精确查询可加引号。'
    '搜索摘要只是线索，重要身份结论需匹配完整姓名并读取可信原文。无关、空结果或上游失败只说明本轮未取得有效证据，'
    '不能推断此人没有公开资料、就是用户本人、是普通人或没有主页。无法验证就简短说明，不输出无关字典条目或重复下一步建议。'
    '同一搜索服务失败后最多尝试一个已启用的替代服务。'
    '回答需附上工具实际返回的原文URL，不得只写“官网、百科”等来源名称；未读取正文不声称已核验。'
)
