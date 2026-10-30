export type CharacterRole = 'mage' | 'paladin' | 'druid';
type Block = {x:number;y:number;w:number;h:number;color:string};
/** Shared pixel geometry keeps the portrait and world sprite identical. */
export function characterBlocks(role:CharacterRole):Block[] {
  const blocks:Block[]=[];
  const r=(x:number,y:number,w:number,h:number,color:string)=>blocks.push({x,y,w,h,color});
  r(13,66,40,4,'#091a21');r(23,57,8,10,'#172735');r(35,57,8,10,'#172735');
  r(21,64,10,4,'#a59a91');r(35,64,10,4,'#a59a91');
  if(role==='mage') {
    r(23,31,20,30,'#4d366f');r(18,37,8,21,'#68448b');r(41,37,7,18,'#68448b');
    r(23,53,23,9,'#79549c');r(28,34,6,24,'#ab7fc8');r(23,48,20,3,'#d5b77b');r(31,48,5,4,'#f0d790');
    r(24,20,19,17,'#3b2c58');r(28,25,11,11,'#e3bca0');r(28,25,11,4,'#a9798e');
    r(28,30,3,2,'#241d38');r(36,30,3,2,'#241d38');r(19,19,29,5,'#9270b6');
    r(24,10,20,10,'#7852a2');r(28,4,12,8,'#8d66b6');r(33,0,7,5,'#af8cd5');
    r(18,48,7,6,'#e3bca0');r(46,44,7,6,'#e3bca0');r(51,20,3,45,'#b3906c');
    r(48,14,9,10,'#68507d');r(50,7,5,15,'#a88bdb');r(47,11,11,7,'#ba99e9');r(50,11,5,7,'#e1d3fb');
  } else if(role==='paladin') {
    r(22,32,23,29,'#986f38');r(18,34,10,13,'#d1a85b');r(39,34,10,13,'#e4bd70');
    r(26,35,15,22,'#dfb46a');r(30,35,7,22,'#f2d891');r(22,51,23,5,'#69513d');r(30,50,7,7,'#f0d58b');r(32,51,3,4,'#b7d8d4');
    r(23,17,21,18,'#d7b471');r(26,13,15,7,'#f0ce83');r(27,23,13,9,'#283b45');r(31,22,5,13,'#e8c581');
    r(24,19,19,4,'#f4db9b');r(31,5,5,10,'#bc7375');r(28,8,11,5,'#dd9e8e');r(45,46,6,7,'#d8b89b');
    r(52,28,3,34,'#c5c9c4');r(50,22,7,25,'#e9e7d5');r(48,46,11,3,'#d5ae62');r(52,50,3,12,'#8b6244');
    r(8,39,17,20,'#634d37');r(11,35,16,22,'#dfb46a');r(14,39,10,19,'#42636c');r(16,55,6,6,'#c5a460');r(17,39,3,15,'#f1d78c');r(13,44,11,3,'#f1d78c');
  } else {
    r(21,34,24,27,'#355c4d');r(17,38,9,19,'#547a59');r(42,37,8,20,'#50764f');r(26,35,10,25,'#789967');
    r(23,53,21,8,'#648951');r(22,48,24,4,'#af9d63');r(32,47,5,6,'#dfcc8a');r(25,20,18,18,'#465640');
    r(28,26,12,11,'#dcbb99');r(28,26,12,4,'#9a8667');r(29,31,3,2,'#293b31');r(37,31,3,2,'#293b31');
    r(24,20,20,6,'#759462');r(30,16,10,7,'#a9b878');r(20,10,4,13,'#b9a478');r(16,7,4,10,'#c8b589');r(11,8,7,3,'#c8b589');r(20,14,8,3,'#c8b589');
    r(43,10,4,13,'#b9a478');r(47,7,4,10,'#c8b589');r(49,8,7,3,'#c8b589');r(39,14,8,3,'#c8b589');
    r(25,39,4,6,'#b1c184');r(29,43,4,5,'#b1c184');r(45,47,7,6,'#dcbb99');r(52,28,3,36,'#99835b');
    r(49,23,7,8,'#93ad69');r(54,17,7,9,'#abc27f');r(49,15,6,8,'#709c69');r(52,21,3,10,'#d7d8a0');
  }
  return blocks;
}

export default function Character({role,size=96}:{role:CharacterRole;size?:number}) {
  return <svg width={size} height={size} viewBox="0 0 64 72" role="img"
    aria-label={{mage:'法师',paladin:'圣骑士',druid:'德鲁伊'}[role]} shapeRendering="crispEdges"
    style={{imageRendering:'pixelated',flexShrink:0}}>
    {characterBlocks(role).map((block,index)=><rect key={index} x={block.x} y={block.y} width={block.w} height={block.h} fill={block.color}/>)}
  </svg>;
}
