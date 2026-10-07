// ============================================================================
// IMS 2.0 - the browser print window (the HTML print door)
// ============================================================================
// Opens a document in a new window and asks for the Windows print dialog ONCE.
// No imports on purpose: the unit label (unitLabel.ts) prints through this, and
// the label's real-browser geometry test (e2e/tests/layout-unit-label.spec.ts)
// imports that module outside Vite, where the API client cannot load.

/** Result of an attempted print: did it go via QZ, or fall back to HTML? */
export type PrintMethod = 'qz' | 'html' | 'failed';
export interface PrintResult {
  method: PrintMethod;
  message: string;
}

export function printHtmlFallback(htmlDocument: string): PrintResult {
  try {
    const win = window.open('', '_blank', 'width=420,height=620');
    if (!win) {
      return {
        method: 'failed',
        message: 'Could not open a print window (popup blocked?).',
      };
    }
    win.document.open();
    win.document.write(htmlDocument);
    win.document.close();
    // Give the barcode SVG a tick to render before invoking print. onload OR
    // the timer (some browsers never fire onload for document.write) -- but
    // only ONE of them: both firing opened the dialog twice, and a second
    // dialog on a label printer is a second set of labels.
    let asked = false;
    const printOnce = () => {
      if (asked) return;
      asked = true;
      try {
        win.focus();
        win.print();
      } catch {
        /* ignore */
      }
    };
    win.onload = printOnce;
    setTimeout(printOnce, 400);
    return { method: 'html', message: 'Opened label in a print window.' };
  } catch {
    return { method: 'failed', message: 'HTML print failed.' };
  }
}
