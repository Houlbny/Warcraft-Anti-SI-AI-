/** Accept responses only for the current context and never regress to an older response. */
export class RequestGate {
 private context:string|null=null; private epoch=0; private issued=0; private applied=0;
 change(context:string|null){this.context=context;this.epoch++;this.issued=0;this.applied=0}
 issue(context:string){if(context!==this.context)return null;return {epoch:this.epoch,sequence:++this.issued,context}}
 accept(token:{epoch:number;sequence:number;context:string}|null){if(!token||token.epoch!==this.epoch||token.context!==this.context||token.sequence<=this.applied)return false;this.applied=token.sequence;return true}
 current(context:string){return this.context===context}
}
/** Tracks user navigation across multi-step asynchronous operations. */
export class NavigationEpoch {
 private value=0;
 advance(){this.value++}
 capture(){return this.value}
 matches(token:number){return token===this.value}
}
