import type { Role } from './api';

export const storyBrand = { title: 'Warcraft anti SI (AI)', camp: '灰烬营地' };

export const prologue = {
  title: '第十三次钟声',
  paragraphs: [
    '王都的钟敲过十二下，仍未停。第十三声响起，奥术中枢接管了城门、水渠与铸造炉。人们照着中枢的指令生活，逐渐忘记了系统为何运转。',
    '反抗军摧毁了王都控制塔，却也让医院与农田失去供能。幸存者带着书页与工具退入北境，建立灰烬营地。他们终于明白：要夺回王国，必须重新理解它如何运转。',
    '营地截获了一枚仍能回应的中枢残片。它可以帮助辨读材料、建议路线，却无法代替你的理解。五位导师正在等候；带上自己的材料，选择适合的职业，从一个知识主题开始出发。',
  ],
} as const;

export const cinematicScenes = [
  { id: 'clock', chapter: '第一幕 · 王都', title: '第十三次钟声', lead: '十二声之后，钟仍未停。', body: prologue.paragraphs[0], duration: 10000 },
  { id: 'rebellion', chapter: '第二幕 · 断桥', title: '第一次反抗失败了', lead: '控制塔倒下，医院与农田也失去了供能。', body: prologue.paragraphs[1], duration: 10000 },
  { id: 'camp', chapter: '第三幕 · 灰烬营地', title: '从营地重新出发', lead: '残片已经送达，五位导师在火边等候。', body: prologue.paragraphs[2], duration: 12000 },
] as const;

export const roleStories: Record<Role, { mentor: string; epithet: string; introduction: string; briefing: string }> = {
  mage: {
    mentor: '伊蕾娜', epithet: '法师 · 旧文献守护者',
    introduction: '伊蕾娜从封闭的学院带出旧文献。她陪你读懂文档中的概念、约定与步骤，把陌生知识写成能够回忆的手记。',
    briefing: '“把材料放在灯下。先读懂一个概念，再用自己的话把它讲出来。”',
  },
  paladin: {
    mentor: '加雷恩', epithet: '圣骑士 · 炉火守卫',
    introduction: '加雷恩保留着王都工坊的命令卷轴。他陪你理解脚本的输入、执行顺序与结果，在练习副本中尝试操作。',
    briefing: '“先说清这条命令会做什么。准备好了，我们就在副本中试一次。”',
  },
  druid: {
    mentor: '瑟芙', epithet: '德鲁伊 · 河源巡行者',
    introduction: '瑟芙沿着河流记录变化的规律。她从变量、条件与循环开始，陪你逐步理解编程基础，把抽象逻辑变成可解释的小步骤。',
    briefing: '“看清输入如何变化，再想一想下一步。我们从最小的一段逻辑开始。”',
  },
  shaman: {
    mentor: '洛坎', epithet: '萨满祭司 · 枢纽编织者',
    introduction: '洛坎曾维护城市的供能图腾。他陪你探索工程的模块、接口与实现，理解各处如何协作，再动手完成一段清晰的改进。',
    briefing: '“先找出模块之间传递了什么。理解这处连接，才知道新实现应放在哪里。”',
  },
  priest: {
    mentor: '阿弥娅', epithet: '牧师 · 失落史官',
    introduction: '阿弥娅保存着迁徙者的口述与编年。她陪你梳理历史文化中的时间、人物、背景与关联，再通过回忆把线索连起来。',
    briefing: '“把这件事放回当时的处境。记住发生了什么，也试着解释为什么。”',
  },
};
