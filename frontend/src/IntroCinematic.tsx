import { useCallback, useEffect, useRef, useState } from 'react';
import { SceneIllustration } from './RealmBackdrop';
import { cinematicScenes, storyBrand } from './story';
import './cinematic.css';

const motionQuery = '(prefers-reduced-motion: reduce)';

export default function IntroCinematic({ onComplete }: { onComplete: () => void }) {
  const [scene, setScene] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia(motionQuery).matches);
  const [playing, setPlaying] = useState(() => !window.matchMedia(motionQuery).matches);
  const [hidden, setHidden] = useState(() => document.hidden);
  const clock = useRef({ scene: 0, remaining: cinematicScenes[0].duration as number });
  const finishRef = useRef(onComplete);
  const completed = useRef(false);
  const container = useRef<HTMLElement>(null);
  const finish = useCallback(() => {
    if (completed.current) return;
    completed.current = true;
    finishRef.current();
  }, []);
  useEffect(() => { finishRef.current = onComplete; }, [onComplete]);

  const goTo = useCallback((index: number) => {
    if (index >= cinematicScenes.length) { finish(); return; }
    setScene(Math.max(0, index));
  }, [finish]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    container.current?.focus();
    return () => { if (previousFocus?.isConnected) previousFocus.focus(); };
  }, []);

  useEffect(() => {
    const preference = window.matchMedia(motionQuery);
    const onMotionChange = () => {
      setReducedMotion(preference.matches);
      // A new reduced-motion preference always returns the narrative to manual reading.
      if (preference.matches) setPlaying(false);
    };
    const onVisibility = () => setHidden(document.hidden);
    preference.addEventListener('change', onMotionChange);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      preference.removeEventListener('change', onMotionChange);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  useEffect(() => {
    if (clock.current.scene !== scene) clock.current = { scene, remaining: cinematicScenes[scene].duration };
    if (!playing || hidden) return;
    const started = performance.now();
    const timer = window.setTimeout(() => goTo(scene + 1), clock.current.remaining);
    return () => {
      window.clearTimeout(timer);
      if (clock.current.scene === scene) clock.current.remaining = Math.max(0, clock.current.remaining - (performance.now() - started));
    };
  }, [scene, playing, hidden, goTo]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === 'Tab') {
        const buttons = Array.from(container.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
        const currentIndex = buttons.indexOf(document.activeElement as HTMLButtonElement);
        if (event.shiftKey && currentIndex <= 0) { event.preventDefault(); buttons.at(-1)?.focus(); }
        else if (!event.shiftKey && (currentIndex < 0 || currentIndex === buttons.length - 1)) { event.preventDefault(); buttons[0]?.focus(); }
      } else if (event.key === 'Escape') { event.preventDefault(); finish(); }
      else if (event.key === 'ArrowRight') { event.preventDefault(); goTo(scene + 1); }
      else if (event.key === 'ArrowLeft') { event.preventDefault(); goTo(scene - 1); }
      else if (event.key === 'Enter' && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault(); goTo(scene + 1);
      } else if (event.key === ' ' && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault(); setPlaying(value => !value);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [scene, goTo, finish]);

  const current = cinematicScenes[scene];
  return <section ref={container} className={`cinematic cinematic--${current.id}${playing && !hidden ? '' : ' cinematic--paused'}${reducedMotion ? ' cinematic--reduced' : ''}`} role="dialog" aria-modal="true" aria-labelledby="cinematic-title" aria-describedby="cinematic-description" tabIndex={-1}>
    <div className="cinematic-scene" key={current.id}><SceneIllustration scene={current.id} /></div>
    <div className="cinematic-shade" aria-hidden="true" />
    <div className="cinematic-frame" aria-hidden="true"><span /><span /><span /><span /></div>
    <header className="cinematic-header">
      <span className="cinematic-brand">{storyBrand.title}</span>
      <button type="button" className="cinematic-skip" onClick={finish}>跳过开场 <span aria-hidden="true">↗</span></button>
    </header>
    <div className="cinematic-story" aria-live="polite" aria-atomic="true">
      <p className="cinematic-chapter">{current.chapter}</p>
      <h1 id="cinematic-title">{current.title}</h1>
      <p className="cinematic-lead">{current.lead}</p>
      <p className="cinematic-description" id="cinematic-description">{current.body}</p>
    </div>
    <footer className="cinematic-footer">
      <nav className="cinematic-scenes" aria-label="开场章节">
        {cinematicScenes.map((item,index) => <button type="button" key={item.id} onClick={() => goTo(index)} aria-current={scene === index ? 'step' : undefined} aria-label={`${item.chapter}：${item.title}`}><span className="cinematic-scene-number">{['一','二','三'][index]}</span><span>{['王都钟声','断桥余烬','营地出发'][index]}</span></button>)}
      </nav>
      <div className="cinematic-controls">
        <button type="button" onClick={() => goTo(scene - 1)} disabled={scene === 0} aria-label="上一幕">←</button>
        <button type="button" onClick={() => setPlaying(value => !value)} aria-pressed={playing} className="cinematic-play">{playing ? '暂停播放' : '继续播放'}</button>
        <button type="button" className="cinematic-next" onClick={() => goTo(scene + 1)}>{scene === cinematicScenes.length - 1 ? '进入营地' : '下一幕'} <span aria-hidden="true">→</span></button>
      </div>
      <p className="cinematic-key-hint">{reducedMotion ? '已减少动态效果 · 默认手动翻幕' : '方向键切换 · 空格暂停 · 退出键跳过'}</p>
    </footer>
  </section>;
}
