import {useEffect,useRef} from 'react';
import Phaser from 'phaser';
import type {Quest} from './api';
import {characterBlocks,type CharacterRole} from './Character';

type WorldProps={quests:Quest[];completed:number;onSelect:(index:number)=>void;role?:CharacterRole;currentQuestId?:string};
type Point={x:number;y:number};
type NodeState='completed'|'current'|'locked';
const colors={mage:0xb597e0,paladin:0xe6c683,druid:0x9aba86};
const labels={explore:'探索',predict:'预测',repair:'修复',feature:'实践'};
const font='"Microsoft YaHei", "PingFang SC", sans-serif';

export default function World(props:WorldProps) {
  const host=useRef<HTMLDivElement>(null),latest=useRef(props),sceneRef=useRef<CampaignForest|null>(null);
  latest.current=props;

  class CampaignForest extends Phaser.Scene {
    ground!:Phaser.GameObjects.Graphics;
    route!:Phaser.GameObjects.Graphics;
    nodes:Phaser.GameObjects.GameObject[]=[];
    player?:Phaser.GameObjects.Container;
    playerArt?:Phaser.GameObjects.Graphics;
    focusRing?:Phaser.GameObjects.Graphics;
    points:Point[]=[];
    focused=-1;
    ready=false;
    reduced=false;
    canvasFocused=false;
    moving?:Phaser.Tweens.Tween;
    pulse?:Phaser.Tweens.Tween;
    previous=new Set<string>();
    activeId='';
    activeRole:CharacterRole='mage';
    media?:MediaQueryList;
    handlers=new AbortController();

    create() {
      sceneRef.current=this;
      this.ground=this.add.graphics().setDepth(0);this.route=this.add.graphics().setDepth(5);
      this.media=window.matchMedia('(prefers-reduced-motion: reduce)');this.reduced=this.media.matches;
      const motion=()=>{this.reduced=this.media!.matches;this.tweens.timeScale=this.reduced?0:1;if(this.reduced)this.sync(true);};
      this.media.addEventListener('change',motion);
      const canvas=this.game.canvas,options={signal:this.handlers.signal};
      canvas.tabIndex=0;canvas.setAttribute('role','application');
      canvas.addEventListener('pointerdown',()=>canvas.focus({preventScroll:true}),options);
      canvas.addEventListener('focus',()=>{this.canvasFocused=true;this.drawFocus();},options);
      canvas.addEventListener('blur',()=>{this.canvasFocused=false;this.drawFocus();},options);
      canvas.addEventListener('keydown',event=>{
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Enter',' '].includes(event.key))return;
        event.preventDefault();
        if(event.key==='Enter'||event.key===' '){this.select(this.focused);return;}
        const direction=event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:1;
        const allowed=latest.current.quests.map((_,index)=>index).filter(index=>this.state(index)!=='locked');
        if(!allowed.length)return;
        this.focused=allowed[Phaser.Math.Clamp(allowed.indexOf(this.focused)+direction,0,allowed.length-1)];this.drawFocus();
      },options);
      this.scale.on('resize',this.layout,this);
      this.events.once(Phaser.Scenes.Events.SHUTDOWN,()=>{
        this.handlers.abort();this.media?.removeEventListener('change',motion);this.scale.off('resize',this.layout,this);
        if(sceneRef.current===this)sceneRef.current=null;
      });
      this.ready=true;this.layout();
    }

    active(){return latest.current.quests.findIndex(q=>q.status!=='completed');}
    state(index:number):NodeState {
      const quest=latest.current.quests[index];if(!quest)return 'locked';
      if(quest.status==='completed')return 'completed';
      return index===this.active()&&String(quest.status)!=='locked'?'current':'locked';
    }

    layout() {
      if(!this.ready)return;
      const width=this.scale.width,height=this.scale.height,count=latest.current.quests.length;
      const columns=Math.max(1,Math.ceil(count/2)),left=width*(width<700?.12:.22),right=width*.87;
      const upper=Math.max(145,height*.28),lower=Math.max(upper+105,Math.min(Math.max(upper+130,height*.54),height-285));
      this.points=Array.from({length:count},(_,index)=>{
        const row=Math.floor(index/columns),column=row%2?columns-1-index%columns:index%columns;
        return {x:columns===1?(left+right)/2:left+(right-left)*column/(columns-1),y:row===0?upper:lower};
      });
      this.drawGround(width,height);this.sync(true);
    }

    drawGround(width:number,height:number) {
      const g=this.ground.clear();g.fillStyle(0x101e26).fillRect(0,0,width,height);
      g.fillStyle(0x152a2e).fillRect(0,height*.19,width,height*.81);
      g.fillStyle(0x1b3434,.45).fillEllipse(width*.58,height*.48,width*.95,height*.75);
      let seed=271;const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
      for(let i=0;i<Math.min(1800,width*height/450);i++)g.fillStyle(i%3?0x29433d:0x3a5145,.4)
        .fillRect(Math.floor(random()*width/4)*4,Math.floor(random()*height/4)*4,4,2);
      const tree=(x:number,y:number,size:number,dark=false)=>{
        g.fillStyle(0x0b1b23,.8).fillRect(x-19*size,y+4,43*size,8*size);
        g.fillStyle(0x3b3c30).fillRect(x-4*size,y-15*size,8*size,25*size);
        g.fillStyle(dark?0x10232b:0x1c3b38).fillRect(x-26*size,y-18*size,52*size,18*size)
          .fillRect(x-20*size,y-38*size,40*size,21*size).fillRect(x-14*size,y-58*size,28*size,21*size).fillRect(x-7*size,y-75*size,14*size,18*size);
        g.fillStyle(dark?0x183039:0x2b4a40).fillRect(x-20*size,y-20*size,17*size,4*size)
          .fillRect(x-14*size,y-40*size,13*size,4*size).fillRect(x-7*size,y-60*size,10*size,4*size);
      };
      for(let i=0;i<Math.ceil(width/32)+3;i++)tree(i*34-25,height*.21+random()*30,.9+random()*.7,true);
      for(let i=0;i<Math.ceil(width/47);i++)tree(i*49+5,height*.24+random()*30,.9+random()*.5);
      for(let i=0;i<10;i++){
        tree(random()*width*.13,height*.34+random()*height*.63,1+random()*.7);
        tree(width*(.92+random()*.08),height*.33+random()*height*.65,1+random()*.7);
      }
      for(let i=0;i<width/34;i++)g.fillStyle(0x26393c).fillRect(i*34,height-59,32,20)
        .fillStyle(0x45534c).fillRect(i*34+2,height-61,29,5);
      for(const x of [width*.16,width*.91]){
        g.fillStyle(0xb79b61,.04).fillCircle(x,height*.46,65);g.fillStyle(0xc9ab6b,.07).fillCircle(x,height*.46,36);
        g.fillStyle(0x657168).fillRect(x-7,height*.46-2,14,39);g.fillStyle(0xd4b37c).fillRect(x-5,height*.46-12,10,13);
        g.fillStyle(0xf0d398).fillRect(x-2,height*.46-10,4,8);
      }
      g.fillStyle(0xb2b9ad,.55).fillRect(width*.78,70,20,20).fillRect(width*.78-4,74,28,12);
      g.fillStyle(0x101e26).fillRect(width*.78+8,67,17,18);
    }

    landmark(point:Point,type:Quest['type'],state:NodeState) {
      const g=this.add.graphics().setPosition(point.x,point.y).setDepth(20),lit=state!=='locked';
      const stone=lit?0x879287:0x3a4d4c,shadow=lit?0x415657:0x293b3d;
      const glow=state==='completed'?0x9bbd99:colors[latest.current.role??'mage'];
      g.fillStyle(0x081a21,.5).fillEllipse(0,13,70,22);g.fillStyle(shadow).fillRect(-25,-1,50,18).fillRect(-31,8,62,10);
      g.fillStyle(stone).fillRect(-27,-2,54,6).fillRect(-32,9,64,4);
      if(type==='explore'){
        g.fillStyle(shadow).fillRect(-21,-42,13,41).fillRect(8,-42,13,41);
        g.fillStyle(stone).fillRect(-24,-47,48,10).fillRect(-19,-36,6,30).fillRect(10,-36,6,30);
        g.fillStyle(lit?glow:0x4a605b).fillRect(-4,-29,8,17).fillRect(-8,-25,16,8);
      }else if(type==='predict'){
        g.fillStyle(shadow).fillRect(-5,-22,10,23).fillRect(-14,-5,28,5);
        g.fillStyle(stone).fillRect(-21,-27,42,6).fillRect(-16,-32,32,6);
        g.fillStyle(lit?glow:0x496263).fillRect(-11,-44,22,12).fillRect(-7,-48,14,20);
        g.fillStyle(lit?0xd9dfc6:0x657977).fillRect(-3,-45,5,5);
      }else if(type==='repair'){
        g.fillStyle(shadow).fillRect(-22,-31,44,31);g.fillStyle(stone).fillRect(-26,-36,52,9).fillRect(-21,-26,8,24).fillRect(13,-26,8,24);
        g.fillStyle(lit?0xc39465:0x52605b).fillRect(-9,-17,18,16);g.fillStyle(lit?0xe5c281:0x748070).fillRect(-4,-22,9,20);
        g.fillStyle(0x5b6761).fillRect(14,-52,8,16).fillRect(10,-55,16,7);
      }else{
        g.fillStyle(lit?0x4d7560:0x334e45).fillRect(-20,-36,40,17).fillRect(-14,-48,28,16);
        g.fillStyle(lit?0x86a572:0x4a6657).fillRect(-16,-38,18,5).fillRect(-9,-48,15,5);
        g.fillStyle(0x74664c).fillRect(-5,-22,10,21);g.fillStyle(lit?glow:0x536757).fillRect(-4,-32,8,9);
      }
      if(!lit){g.fillStyle(0x9aab9c).fillRect(16,-15,12,10).fillRect(18,-20,8,5);g.fillStyle(0x293e40).fillRect(21,-17,3,4).fillRect(21,-12,3,4);}
      this.nodes.push(g);
    }

    sync(resize=false) {
      if(!this.ready)return;if(this.points.length!==latest.current.quests.length){this.layout();return;}
      const quests=latest.current.quests,role=latest.current.role??'mage';
      this.nodes.forEach(object=>object.destroy());this.nodes=[];this.pulse?.stop();
      const route=this.route.clear();
      for(let i=1;i<this.points.length;i++){
        const a=this.points[i-1],b=this.points[i],done=this.state(i-1)==='completed';
        route.lineStyle(31,0x0d2028,.95).lineBetween(a.x,a.y+17,b.x,b.y+17);
        route.lineStyle(23,done?0x667465:0x354a48).lineBetween(a.x,a.y+17,b.x,b.y+17);
        route.lineStyle(15,done?0x7b806b:0x40534d,.65).lineBetween(a.x,a.y+16,b.x,b.y+16);
        const distance=Phaser.Math.Distance.Between(a.x,a.y,b.x,b.y);
        for(let step=15;step<distance;step+=23){const t=step/distance;route.fillStyle(done?0xa2a387:0x657161,.4)
          .fillRect(Math.round((a.x+(b.x-a.x)*t)/2)*2-3,Math.round((a.y+(b.y-a.y)*t)/2)*2+14,7,4);}
      }
      const newlyCompleted:Point[]=[],current=this.active();
      quests.forEach((quest,index)=>{
        const point=this.points[index],state=this.state(index);
        if(state==='current'){
          const halo=this.add.circle(point.x,point.y+7,51,colors[role],.08).setDepth(6);
          const ring=this.add.ellipse(point.x,point.y+12,89,31).setStrokeStyle(2,colors[role],.8).setDepth(7);this.nodes.push(halo,ring);
          if(!this.reduced)this.pulse=this.tweens.add({targets:[halo,ring],alpha:.35,duration:1200,yoyo:true,repeat:-1});
          const marker=this.add.graphics().setPosition(point.x,point.y-72).setDepth(25);
          marker.fillStyle(colors[role]).fillRect(-3,-6,6,6).fillTriangle(-7,0,7,0,0,7);this.nodes.push(marker);
        }
        this.landmark(point,quest.type,state);
        const labelWidth=Math.min(148,this.scale.width/Math.max(3,Math.ceil(quests.length/2))*.74);
        const color=state==='locked'?'#81968d':state==='completed'?'#acc8ad':'#ede3c4';
        const titleLimit=Math.max(5,Math.min(12,Math.floor(labelWidth/(this.scale.width<700?11:13))));
        const title=quest.title.length>titleLimit?quest.title.slice(0,titleLimit-1)+'…':quest.title;
        const text=this.add.text(point.x,point.y+29,`${String(index+1).padStart(2,'0')} · ${labels[quest.type]}${state==='completed'?'  ✓':''}\n${title}`,
          {fontFamily:font,fontSize:this.scale.width<700?'11px':'13px',color,align:'center',lineSpacing:6,
            wordWrap:{width:labelWidth},backgroundColor:state==='current'?'#193136':'#142b2d',padding:{x:7,y:6}}).setOrigin(.5,0).setDepth(40).setAlpha(state==='locked'?.65:1);
        this.nodes.push(text);
        if(state!=='locked'){
          const zone=this.add.zone(point.x,point.y-5,82,96).setDepth(45).setInteractive({useHandCursor:true});
          zone.on('pointerover',()=>{text.setColor('#f4dfad');this.focused=index;this.drawFocus();});zone.on('pointerout',()=>text.setColor(color));
          zone.on('pointerdown',()=>this.select(index));this.nodes.push(zone);
        }
        if(quest.status==='completed'&&!this.previous.has(quest.id)&&this.activeId)newlyCompleted.push(point);
      });
      const selected=quests.findIndex(quest=>quest.id===latest.current.currentQuestId);
      const destination=selected>=0&&this.state(selected)!=='locked'?selected:current<0?Math.max(0,quests.length-1):current;
      if(this.focused<0||!quests[this.focused]||this.state(this.focused)==='locked')this.focused=destination;
      this.drawFocus();const nextId=quests[destination]?.id??'';
      if(!this.player){this.player=this.add.container(this.scale.width*.5,this.scale.height*.45).setDepth(80);this.playerArt=this.add.graphics();this.player.add(this.playerArt);}
      if(this.activeRole!==role||!this.playerArt!.getData('drawn')){
        this.playerArt!.clear();characterBlocks(role).forEach(block=>this.playerArt!.fillStyle(parseInt(block.color.slice(1),16)).fillRect(block.x-32,block.y-65,block.w,block.h));
        this.playerArt!.setData('drawn',true);this.activeRole=role;
      }
      if(resize||this.activeId!==nextId)this.moveTo(destination,resize||!this.activeId);
      this.activeId=nextId;this.previous=new Set(quests.filter(q=>q.status==='completed').map(q=>q.id));
      newlyCompleted.forEach(point=>this.celebrate(point));
      this.game.canvas.setAttribute('aria-label',`关卡路线，${latest.current.completed}/${quests.length} 已完成。左右方向键选择已解锁关卡，回车进入。${current>=0?'当前目标：'+quests[current]?.title:quests.length?'本章已完成':'等待生成关卡'}`);
    }

    drawFocus(){this.focusRing?.destroy();const point=this.points[this.focused];if(!this.canvasFocused||!point)return;
      this.focusRing=this.add.graphics().setDepth(60);this.focusRing.lineStyle(2,0xf0ddab).strokeRoundedRect(point.x-43,point.y-59,86,133,3);}
    moveTo(index:number,instant=false){if(!this.player)return;const point=this.points[index]??{x:this.scale.width*.51,y:this.scale.height*.43};this.moving?.stop();
      if(instant||this.reduced){this.player.setPosition(point.x+31,point.y+9);return;}
      this.moving=this.tweens.add({targets:this.player,x:point.x+31,y:point.y+9,duration:700,ease:'Sine.easeInOut',onComplete:()=>{if(this.state(index)==='current')this.celebrate(point,false);}});}
    select(index:number){if(index<0||this.state(index)==='locked')return;this.focused=index;this.drawFocus();this.moveTo(index);latest.current.onSelect(index);}
    celebrate(point:Point,complete=true){if(this.reduced)return;
      const ring=this.add.ellipse(point.x,point.y+9,40,17).setStrokeStyle(2,complete?0xb5d09e:colors[this.activeRole],.8).setDepth(60);
      this.tweens.add({targets:ring,scaleX:2.5,scaleY:2.5,alpha:0,duration:800,onComplete:()=>ring.destroy()});
      if(complete)for(let i=0;i<8;i++){const spark=this.add.rectangle(point.x-24+i*7,point.y-12,3,3,0xd2d7a0).setDepth(65);
        this.tweens.add({targets:spark,y:spark.y-25-i%3*10,alpha:0,duration:700+i*40,onComplete:()=>spark.destroy()});}
    }
  }

  useEffect(()=>{
    if(!host.current)return;
    const game=new Phaser.Game({type:Phaser.AUTO,parent:host.current,width:host.current.clientWidth||1000,height:host.current.clientHeight||700,
      backgroundColor:'#101e26',pixelArt:true,antialias:false,scene:CampaignForest,scale:{mode:Phaser.Scale.RESIZE},
      audio:{noAudio:true},banner:false,render:{roundPixels:true},input:{keyboard:false}});
    const observer=new ResizeObserver(()=>{if(host.current&&game.isBooted)game.scale.resize(host.current.clientWidth,host.current.clientHeight);});
    observer.observe(host.current);return()=>{observer.disconnect();game.destroy(true);sceneRef.current=null;};
  },[]);
  useEffect(()=>{sceneRef.current?.sync();},[props.quests,props.completed,props.role,props.currentQuestId]);
  return <div className="world-campaign" style={{position:'absolute',inset:0,overflow:'hidden',background:'#101e26'}}>
    <style>{'.world-campaign .world-canvas-host{position:absolute;inset:0;width:100%;height:100%}.world-campaign canvas{display:block;width:100%;height:100%}.world-campaign canvas:focus-visible{outline:2px solid #e6cd91;outline-offset:-5px}'}</style>
    <div className="world-canvas-host" ref={host}/>
  </div>;
}
