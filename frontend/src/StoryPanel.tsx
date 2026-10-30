import {FolderOpen} from 'lucide-react';
import {roles} from './api';
import {Modal} from './Panels';
import {prologue,roleStories,storyBrand} from './story';

export default function StoryPanel({onClose,onImport}:{onClose:()=>void;onImport?:()=>void}){
 return <Modal title="战地手记" onClose={onClose} className="story-panel">
  <div className="story-heading"><span className="story-label">游戏剧情 · {storyBrand.camp}</span><h1>{prologue.title}</h1></div>
  {onImport&&<div className="story-import-actions"><button className="button primary" onClick={onImport}>导入项目<FolderOpen size={17}/></button><button className="text-button" onClick={onImport}>跳过故事，导入项目</button></div>}
  <div className="story-prologue">{prologue.paragraphs.map((paragraph,i)=><p key={i}>{paragraph}</p>)}</div>
  <section className="story-mentors" aria-label="营地导师"><h2>营地的三位接应者</h2>{roles.map(role=>{const story=roleStories[role.id];return <article key={role.id}><small>{story.epithet}</small><h3>{story.mentor}</h3><p>{story.introduction}</p><p className="story-briefing">{story.briefing}</p></article>})}</section>
  <p className="story-source-note">营地称 AI 顾问为“中枢残片”。这是剧情身份；AI 生成内容、AI 评审与本机运行结果仍分别记录。</p>
 </Modal>
}
