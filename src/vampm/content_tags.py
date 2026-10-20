"""Independent, evidence-grounded content tags and durable generation state."""
import json
import re

from .summary_content import CONTENT_RULES
from .summary_state import SummaryState
from .util import stamp

VOCABULARY = {
    '内容提示': ['全年龄向','成人向','裸露','亲密互动','暴力题材','恐怖题材'],
    '人物呈现': ['女性形象','男性形象','中性外观','多人角色','幼态外观','年轻感','成熟感','年长感'],
    '造型风格': ['写实','半写实','动漫风','二次元','卡通风','幻想风','科幻风','古风','现代风','复古风','赛博朋克','哥特风','可爱风','清冷风','活泼感','优雅感','冷峻感'],
    '外貌特征': ['圆脸','长脸','柔和五官','轮廓鲜明','雀斑','痣','疤痕','纹身','淡妆','浓妆','浅肤色','深肤色','纤细体型','健壮体型','精灵耳','兽耳','角','尾巴'],
    '头发': ['黑发','棕发','金发','银白发','红发','粉发','蓝发','紫发','短发','及肩发','长发','直发','卷发','波浪发','齐刘海','侧分','中分','单马尾','双马尾','盘发','辫发'],
    '服饰': ['日常装','连衣裙','短裙','长裙','衬衫','外套','裤装','礼服','旗袍','和服','汉服','盔甲','制服','运动装','泳装','内衣','睡衣','丝袜','长袜','手套','高跟鞋','靴子','首饰','头饰','眼镜','蕾丝','皮革风','金属装饰','宽松剪裁','贴身剪裁','黑色','白色','红色','粉色','蓝色','多色版本'],
    '场景与环境': ['人物展示','舞蹈','剧情','互动场景','动作动画','姿势预设','摄影棚','室内','室外','卧室','客厅','浴室','酒吧','舞台','街道','森林','海滩','泳池','船舱','城堡','太空','白天','夜景','暖色灯光','冷色灯光','柔光','运镜','带音乐','双人互动'],
    '功能与资源': ['外观预设','服装预设','发型预设','皮肤贴图','妆容贴图','身体变形','面部变形','灯光预设','相机预设','物理插件','动画插件','界面插件','渲染插件','音频资源','环境模型','道具模型'],
}
TAG_ALIASES={'R18':'成人向','r18':'成人向','R-18':'成人向','男性':'男性形象','女性':'女性形象'}

TAG_RULES = '''标签只描述本包实际包含的内容。优先使用预设，允许有证据的新标签；通常4–12个，信息少可更少或为空，最有辨识度的4个放前面。
外观风格统一使用预设词，如“可爱风”“年轻感”“幼态外观”，不把已知年龄事实自动转换成风格标签。
R18统一为“成人向”，但该标签不证明角色成年。非性化裸露不自动等于成人向；没有成人证据也不自动标全年龄向。
不生成未知、待确认等占位标签。仅标签任务不得修改简介或分类。
'''


def instruction(target, summary_instruction):
    if target == 'summary': return summary_instruction
    base = summary_instruction.rsplit('分类只能是：',1)[0] if target == 'both' else CONTENT_RULES
    fields = 'key、category、summary、tags' if target == 'both' else 'key、tags'
    return base+'\n'+TAG_RULES+'\n预设标签（允许有证据的新标签）：\n'+json.dumps(VOCABULARY,ensure_ascii=False)+'\n严格按提供的JSON schema返回items，每项包含'+fields+'；tags为短字符串数组，key原样返回。'

def normalize_tags(values):
    if not isinstance(values,list) or len(values)>24: raise ValueError('标签必须为最多24项的列表')
    result=[]
    for value in values:
        if not isinstance(value,str): raise ValueError('标签必须为文字')
        value=re.sub(r'\s+',' ',value).strip().lstrip('#')
        value=TAG_ALIASES.get(value,value)
        if not value or len(value)>30 or re.search(r'[<>\x00-\x1f]',value): raise ValueError('标签须为1–30字的短语')
        if value.casefold() not in {x.casefold() for x in result}:result.append(value)
    return result

class TagState:
    @staticmethod
    def setup(db):
        db.execute('''CREATE TABLE IF NOT EXISTS package_tags (
            pkgkey TEXT PRIMARY KEY, tags TEXT NOT NULL DEFAULT '[]', source TEXT,
            completed INTEGER NOT NULL DEFAULT 0, fingerprints TEXT NOT NULL DEFAULT '[]',
            run_id TEXT NOT NULL DEFAULT '', updated TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_tags_run ON package_tags(run_id)')

    @staticmethod
    def observe(db,key,size,mtime):
        state=db.execute('SELECT * FROM package_tags WHERE pkgkey=?',(key,)).fetchone()
        if state and state['completed'] and f'{size}:{mtime}' not in json.loads(state['fingerprints']):
            db.execute('UPDATE package_tags SET completed=0,updated=? WHERE pkgkey=?',(stamp(),key))

    @staticmethod
    def complete(state,rows):
        return SummaryState.complete(state,rows,'tags') # An intentionally empty result is complete.

    @staticmethod
    def save(db,key,tags,source,run_id=None):
        old=db.execute('SELECT run_id FROM package_tags WHERE pkgkey=?',(key,)).fetchone()
        rows=db.execute("SELECT size,mtime FROM files WHERE pkgkey=? AND area IN ('active','vault')",(key,)).fetchall()
        state=dict(pkgkey=key,tags=json.dumps(normalize_tags(tags),ensure_ascii=False),source=source,
                   completed=int(source in ('ai','manual')),fingerprints=json.dumps(sorted({SummaryState.fingerprint(r) for r in rows})),
                   run_id=run_id if run_id is not None else (old['run_id'] if old else ''),updated=stamp())
        db.execute('INSERT OR REPLACE INTO package_tags VALUES(?,?,?,?,?,?,?)',tuple(state.values()))
        return state


def outputs(target):
    if target not in ('summary','tags','both'): raise ValueError('请选择简介与标签、仅简介或仅标签')
    return ('summary','tags') if target=='both' else (target,)

def needed(p,target,mode,run_id):
    return [field for field in outputs(target) if not p.get(field+'_generated') or
            (mode=='all' and p.get(field+'_run_id')!=run_id)]
