// The forced sign-out (a 401 the client cannot refresh) tells the POS store
// by this event, so it runs THE one sign-out step (park a pending bill, then
// clear the draft). A file of its own: client.ts cannot import the store
// (posStore -> gstRuntime -> client is an import cycle).
export const SIGNED_OUT_EVENT = 'ims:signed-out';
