// ============================================================================
// IMS 2.0 - POS delivery-completion screen (Wave 4, owner spec 12)
// ============================================================================
// Shown straight after Mark-delivered. Same shape as the sale twin, with the
// delivery wording and document set: the words "final tax invoice" are used
// HERE and only here (at sale time the customer document is an ORDER RECEIPT).
//
// Deliberately a thin wrapper: the layout, the print/send doors and the
// "My day" scorecard live once in SaleCompleteScreen.tsx's CompletionScreen.
// A second copy of that screen is exactly how this repo's rules drift apart;
// the only difference between the two is a stage flag.
//
// MOUNT (the parent wires it; do not edit the parent from here):
//
//   import DeliveryCompleteScreen from './DeliveryCompleteScreen';
//   ...
//   <DeliveryCompleteScreen
//     orderId={order.id}
//     orderNumber={order.orderNumber}
//     salespersonId={order.salespersonId}
//     salespersonName={order.salespersonName}
//     onDone={() => setOrder(null)}
//   />
//
// It fills its flex/grid cell (h-full) and scrolls INTERNALLY -- the POS page
// itself must never scroll (spec 11b).

import { CompletionScreen, type CompletionScreenProps } from './SaleCompleteScreen';

export type DeliveryCompleteScreenProps = Omit<CompletionScreenProps, 'stage' | 'jobId'>;

export function DeliveryCompleteScreen(props: DeliveryCompleteScreenProps) {
  // No jobId here on purpose: the workshop job card is a SALE-time print (it
  // goes with the frame into the lab), not a handover document.
  return <CompletionScreen {...props} stage="DELIVERY" />;
}

export default DeliveryCompleteScreen;
