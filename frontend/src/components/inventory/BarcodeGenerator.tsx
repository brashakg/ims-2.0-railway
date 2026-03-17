// ============================================================================
// IMS 2.0 - Barcode Generator Component
// ============================================================================
// Generate and display barcodes for products

import { useEffect, useRef } from 'react';
import JsBarcode from 'jsbarcode';
import { Download, Printer } from 'lucide-react';

interface BarcodeGeneratorProps {
  value: string;
  format?: 'CODE128' | 'EAN13' | 'UPC' | 'CODE39';
  width?: number;
  height?: number;
  displayValue?: boolean;
  productName?: string;
  price?: number;
  onGenerate?: (barcode: string) => void;
}

export function BarcodeGenerator({
  value,
  format = 'CODE128',
  width = 2,
  height = 50,
  displayValue = true,
  productName,
  price,
  onGenerate,
}: BarcodeGeneratorProps) {
  const barcodeRef = useRef<SVGSVGElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (barcodeRef.current && value) {
      try {
        JsBarcode(barcodeRef.current, value, {
          format: format,
          width: width,
          height: height,
          displayValue: displayValue,
          margin: 10,
          fontSize: 14,
        });

        // Call onGenerate callback if provided
        if (onGenerate) {
          onGenerate(value);
        }
      } catch (error) {
        console.error('Error generating barcode:', error);
      }
    }
  }, [value, format, width, height, displayValue, onGenerate]);

  const handleDownload = () => {
    if (!barcodeRef.current) return;

    // Convert SVG to canvas
    const svg = barcodeRef.current;
    const svgData = new XMLSerializer().serializeToString(svg);
    const canvas = canvasRef.current;

    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const img = new Image();
    img.onload = () => {
      canvas.width = img.width;
      canvas.height = img.height + (productName ? 40 : 0) + (price ? 20 : 0);

      // White background
      ctx.fillStyle = 'white';
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      // Draw barcode
      const yOffset = productName ? 30 : 0;
      ctx.drawImage(img, 0, yOffset);

      // Add product name if provided
      if (productName) {
        ctx.fillStyle = 'black';
        ctx.font = 'bold 14px Arial';
        ctx.textAlign = 'center';
        ctx.fillText(productName, canvas.width / 2, 20);
      }

      // Add price if provided
      if (price) {
        ctx.fillStyle = 'black';
        ctx.font = 'bold 16px Arial';
        ctx.textAlign = 'center';
        ctx.fillText(`₹${price.toFixed(2)}`, canvas.width / 2, canvas.height - 5);
      }

      // Download
      const link = document.createElement('a');
      link.download = `barcode-${value}.png`;
      link.href = canvas.toDataURL('image/png');
      link.click();
    };

    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
  };

  const handlePrint = (labelSize: 'standard' | 'thermal' = 'standard') => {
    const printWindow = window.open('', '_blank');
    if (!printWindow || !barcodeRef.current) return;

    const svg = barcodeRef.current;
    const svgData = new XMLSerializer().serializeToString(svg);

    // Thermal label: 50mm × 25mm (common Zebra/TSC label size)
    // Standard: A4 sheet with label layout
    const isThermal = labelSize === 'thermal';

    printWindow.document.write(`
      <!DOCTYPE html>
      <html>
        <head>
          <title>Print Barcode - ${value}</title>
          <style>
            body {
              margin: 0;
              padding: ${isThermal ? '2mm' : '20px'};
              font-family: Arial, sans-serif;
              display: flex;
              flex-direction: column;
              align-items: center;
            }
            .label {
              border: 1px dashed #ccc;
              padding: ${isThermal ? '1mm 2mm' : '10px'};
              margin: ${isThermal ? '0' : '10px'};
              text-align: center;
              width: ${isThermal ? '46mm' : '250px'};
              ${isThermal ? 'height: 21mm; overflow: hidden;' : ''}
            }
            .product-name {
              font-weight: bold;
              font-size: ${isThermal ? '8px' : '14px'};
              margin-bottom: ${isThermal ? '1px' : '10px'};
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }
            .barcode-wrap svg {
              ${isThermal ? 'max-width: 44mm; height: 12mm;' : ''}
            }
            .price {
              font-weight: bold;
              font-size: ${isThermal ? '10px' : '16px'};
              margin-top: ${isThermal ? '0' : '10px'};
            }
            @media print {
              .no-print { display: none; }
              .label {
                border: none;
                page-break-after: always;
              }
              @page {
                ${isThermal ? 'size: 50mm 25mm; margin: 1mm;' : ''}
              }
            }
          </style>
        </head>
        <body>
          <div class="label">
            ${productName ? `<div class="product-name">${productName}</div>` : ''}
            <div class="barcode-wrap">${svgData}</div>
            ${price ? `<div class="price">₹${price.toLocaleString('en-IN')}</div>` : ''}
          </div>
          <button class="no-print" onclick="window.print(); window.close();" style="margin-top: 20px; padding: 10px 20px; cursor: pointer;">
            Print
          </button>
        </body>
      </html>
    `);
    printWindow.document.close();
  };

  if (!value) {
    return (
      <div className="text-center text-gray-500 py-4">
        No barcode value provided
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Product Name */}
      {productName && (
        <div className="text-center font-medium text-gray-900">
          {productName}
        </div>
      )}

      {/* Barcode SVG */}
      <div className="flex justify-center bg-white p-4 rounded-lg border border-gray-200">
        <svg ref={barcodeRef}></svg>
      </div>

      {/* Price */}
      {price && (
        <div className="text-center font-bold text-lg text-gray-900">
          ₹{price.toFixed(2)}
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex gap-2 justify-center flex-wrap">
        <button
          onClick={handleDownload}
          className="btn-outline text-sm flex items-center gap-2"
        >
          <Download className="w-4 h-4" />
          Download
        </button>
        <button
          onClick={() => handlePrint('standard')}
          className="btn-primary text-sm flex items-center gap-2"
        >
          <Printer className="w-4 h-4" />
          Print (A4)
        </button>
        <button
          onClick={() => handlePrint('thermal')}
          className="btn-outline text-sm flex items-center gap-2"
          title="50mm × 25mm thermal label"
        >
          <Printer className="w-4 h-4" />
          Print (Thermal)
        </button>
      </div>

      {/* Hidden canvas for image generation */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </div>
  );
}

export default BarcodeGenerator;
