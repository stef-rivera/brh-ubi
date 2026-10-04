// Preserve the native inbound space: shared lines can route replies differently
// from synthesized DMs even when both expose the same conversation id.
export async function resolveSpace(im, recipient, inboundSpaces, routes, canonical){
  const key=canonical(recipient);
  if(inboundSpaces.has(key)) return inboundSpaces.get(key);
  const route=routes[key];
  if(route?.id) return im.space.get(route.id,route.phone ? {phone:route.phone} : undefined);
  return im.space.create(await im.user(recipient));
}
export function safeDiagnostic(error, secrets=[]){
  const clean=value=>{
    let text=String(value||'');
    for(const secret of secrets.filter(Boolean)) text=text.replaceAll(secret,'[redacted]');
    return text.replace(/[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}/g,'[token]')
      .replace(/\+?\d{10,15}/g,'[phone]').replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g,'[email]').slice(0,500);
  };
  return {name:clean(error?.name),code:clean(error?.code),grpcCode:clean(error?.grpcCode),
          message:clean(error?.message),causeCode:clean(error?.cause?.code),causeMessage:clean(error?.cause?.message)};
}
