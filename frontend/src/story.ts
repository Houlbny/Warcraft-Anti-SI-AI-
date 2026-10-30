import type { Quest, Role } from './api';

export type StoryBeat = {
  eyebrow: string;
  title: string;
  body: string;
  speaker: string;
};

export const storyBrand = { title: 'Warcraft-anti-SI', camp: '灰烬营地' };

export const prologue: { title: string; paragraphs: readonly string[] } = {
  title: '第十三次钟声',
  paragraphs: [
    '王都的钟敲过十二下，仍未停。第十三声传到北境时，各城的门同时落闸；奥术中枢接管了水渠、炉火与守城法阵。工坊依旧交货，却再没有学徒能说清炉火为何亮起。',
    '多年以后，一名信使带着半块城门铭牌抵达灰烬营地。他穿过了七座仍在运转的哨站，沿途无人能读懂墙上的旧记录。第八座哨站没有回音，只有灯还亮着。',
    '营地决定派你前去。帐中的三位导师摊开残缺地图：一位保存着旧语言的课本，一位曾守卫王都的枢纽，一位记得河流改道前的森林。你将选一人同行，接手那座仍在运转、却尚不熟悉的据点。',
    '临行前，信使交出一枚会说话的晶片——营地截获的“中枢残片”。它可以拟定路线、提出建议，也可能判断失误。导师把晶片装进匣中，又递给你一本空白手记：“它说过什么，与我们亲自查明什么，分开记。”',
  ],
};

export const roleStories: Record<Role, {
  mentor: string;
  epithet: string;
  introduction: string;
  briefing: string;
}> = {
  mage: {
    mentor: '伊蕾娜',
    epithet: '法师 · 旧语抄写员',
    introduction: '封城那夜，伊蕾娜从焚毁的学院带出一本初学课本。她会陪你辨认语言、拆开短小逻辑，在每次推断前留下足够线索。',
    briefing: '“门上的灯还亮着。先弄清眼前这一小段如何工作，再决定下一步；不认识的地方，我们逐句看。”',
  },
  paladin: {
    mentor: '加雷恩',
    epithet: '圣骑士 · 断桥守卫',
    introduction: '加雷恩曾守着通往王都的输能桥。桥断之后，他沿废墟追查每一处连接；这一次，他带你从入口追到核心，辨清职责与实现原理。',
    briefing: '“哨站仍在运转，说明各处仍有联系。从入口开始，把命令经过哪里、由谁完成画清楚。”',
  },
  druid: {
    mentor: '瑟芙',
    epithet: '德鲁伊 · 河源巡行者',
    introduction: '中枢改道河流后，瑟芙走遍干涸的支流与新生的湿地。她会带你看清整体依赖与边界，再为据点尝试一个有分寸的扩展。',
    briefing: '“先看这座据点靠什么维持，又影响着哪里。要添一条新路，就得知道它会穿过谁的土地。”',
  },
};

const scenes = [
  { title: '哨站的灯', body: '入夜，你们抵达第八哨站。窗内灯火未灭，门边只有一本被雨浸湿的值守簿。导师让你先观察，再落笔。', outcome: '{mentor}把值守簿移到灯下，替你晾开潮湿的纸页。你留下抵达后的第一笔记录，门外的风吹动了通往内院的布帘。' },
  { title: '无人换班', body: '内院的铜铃按时响起，却没有守卫来换班。导师从门后找到一张旧班表，末尾停在封城那天。', outcome: '{mentor}抄下班表末尾的名字，将原件放回门后的木匣。你把日期记在页边，铜铃又响了一次，空院里仍没有脚步声。' },
  { title: '墙下的回声', body: '院墙内传来低沉回响。导师拂去墙上的灰尘，露出通往各处的旧标记；其中一条延伸到地下。', outcome: '{mentor}举着灯，你将墙上的旧标记拓在薄纸上。收起拓片时，灯光照出了石阶边磨损的扶手，上面还缠着一截布条。' },
  { title: '地下水门', body: '石阶尽头，水轮仍在转动。导师拾起一片来自上游的叶子：哨站之外，还有地方向这里输送水流。', outcome: '{mentor}将那片叶子夹进手记，在旁边写下发现它的位置。你拧去衣角的水，跟着提灯走上石阶，身后的水轮声渐渐远了。' },
  { title: '晶片的另一端', body: '蓝光从一座旧传讯台透出。中枢残片忽然念出陌生地名，导师合上匣盖，先请你留下自己的判断。', outcome: '{mentor}在你的记录旁另起一栏，写下晶片念出的地名。你们把匣子包进厚布；传讯台的微光里，一枚旧钥匙躺在桌角。' },
  { title: '西塔来信', body: '西塔里积满未送出的信件。最近一封写着：“城门尚闭，请保留北面的通路。”落款是一名普通守门人。', outcome: '{mentor}把守门人的信装进新信封，保留了原来的署名。你在封口写下发现信件的塔楼，抬头时，北面的山谷已透出晨光。' },
  { title: '山谷旧桥', body: '晨雾散开，桥对岸排列着更多哨站的灯。导师没有催你过桥，而是先在地图上圈出这座据点与外界的连接。', outcome: '{mentor}收好摊在桥栏上的地图，你用石块压住留下的营地路标。风穿过山谷，对岸的灯仍一盏接着一盏，伸向看不见的远处。' },
  { title: '被封存的图室', body: '桥头侧门通向旧图室。地图上的王国远大于你走过的路，数十条线路在王都汇合，末端写着“奥术中枢”。', outcome: '{mentor}抄下图室中的北境地名，你将沿途的记录依次排在桌上。纸页只占了大地图的一角，王都的位置仍隔着数条山脉。' },
  { title: '归途上的火光', body: '暮色里，灰烬营地的信使在坡下点起火。他带来空白纸页，等你说明一路看见了什么、还有什么没能确定。', outcome: '{mentor}替信使压住被风掀起的纸页，你把这一程的记录交到他手中。信使系紧防雨布，分给你们半块面饼，火光照亮了回营的路。' },
  { title: '营地的长桌', body: '你们回到灰烬营地，长桌上腾出一块地方。导师摊开本次路线，请你把最后一段记录补齐，再合上手记。', outcome: '{mentor}在手记封面写下第八哨站的名字，将它推到长桌中央。信使端来热水，守夜人坐下翻开第一页，帐外传来新一班换岗声。' },
] as const;

function sceneIndex(quest: Quest, total: number): number {
  const count = Math.max(1, total);
  const order = Math.max(0, Math.min(quest.order, count - 1));
  return count === 1 ? 0 : Math.round(order * (scenes.length - 1) / (count - 1));
}

const roleRemarks: Record<Role, Record<Quest['type'], string>> = {
  mage: { explore: '“先读懂这一小段。”', predict: '“先写下推断，再核对依据。”', repair: '“按题目逐步判断，别急着改动。”', feature: '“用已弄懂的语言，尝试这一步。”' },
  paladin: { explore: '“找清入口与职责。”', predict: '“沿实现链路解释推断。”', repair: '“先确认影响范围与依据。”', feature: '“先认清新行为该由谁负责。”' },
  druid: { explore: '“留意它与周围的联系。”', predict: '“把输入与边界一起考虑。”', repair: '“留意改动会影响哪些依赖。”', feature: '“新路接入后，旧路也要查验。”' },
};

export function questStory(quest: Quest, role: Role, total: number): StoryBeat {
  const scene = scenes[sceneIndex(quest, total)];
  return {
    eyebrow: '剧情 · 远征手记',
    title: scene.title,
    body: `${scene.body}${roleRemarks[role][quest.type]}`,
    speaker: roleStories[role].mentor,
  };
}

export function questCompletionStory(quest: Quest, role: Role, chapterComplete: boolean, total = 10): StoryBeat {
  const mentor = roleStories[role].mentor;
  const scene = scenes[sceneIndex(quest, total)];
  return {
    eyebrow: '剧情 · 行程记录',
    title: chapterComplete ? '送往北境的手记' : '手记添了一页',
    body: chapterComplete
      ? `灰烬营地的长桌上，多了一份第八哨站的手记。${mentor}将它交给北行的信使，封面下夹着那名守门人的来信。王都仍未开门，远方的哨站仍亮着灯；你们这一程的记录，正被装上明早出发的马鞍。`
      : scene.outcome.replace('{mentor}', mentor),
    speaker: roleStories[role].mentor,
  };
}

export function generationStory(stage: string | undefined, role: Role): string {
  const mentor = roleStories[role].mentor;
  const preparation: Record<Role, string> = {
    mage: '伊蕾娜整理课本，在空白页边留出逐步讲解的位置。',
    paladin: '加雷恩把旧桥的结构图压在桌角，准备沿入口与连接规划路线。',
    druid: '瑟芙在地图上铺开细绳，标记据点之间的依赖与边界。',
  };
  switch (stage) {
    case 'analyzing': return `${preparation[role]}匣中的中枢残片正在协助辨读材料。`;
    case 'planning': return `${mentor}摊开远征手记，听中枢残片念出沿途的地名。营地的灯火映在地图上，信使仍守在帐外。`;
    case 'quests': return `${mentor}逐页整理出发前的任务，用细绳将纸页束好。长桌另一端，守夜人添了一截新烛。`;
    case 'checking': return `${mentor}对照路线核对行囊，清点纸页、灯油与回程的信封。帐外传来牵马的脚步声。`;
    default: return `${preparation[role]}信使在帐外等候出发的消息。`;
  }
}
