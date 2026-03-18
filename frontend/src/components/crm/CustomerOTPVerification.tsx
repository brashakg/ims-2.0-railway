// ============================================================================
// OTP-Based Customer Verification (Customer Creation Flow)
// ============================================================================
// During customer creation: Send OTP button next to phone number
// Mock OTP display for testing (shows toast: "OTP sent: 1234")
// Real implementation would integrate WhatsApp/SMS API

import { useState } from 'react';
import { Phone, Send, CheckCircle, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';

interface CustomerOTPVerificationProps {
  phoneNumber: string;
  onVerified?: (verified: boolean) => void;
  disabled?: boolean;
}

export function CustomerOTPVerification({ phoneNumber, onVerified, disabled }: CustomerOTPVerificationProps) {
  const toast = useToast();
  const [isLoading, setIsLoading] = useState(false);
  const [otpSent, setOtpSent] = useState(false);
  const [mockOTP, setMockOTP] = useState<string | null>(null);
  const [userOTP, setUserOTP] = useState('');
  const [isVerified, setIsVerified] = useState(false);

  const handleSendOTP = async () => {
    if (!phoneNumber || phoneNumber.length < 10) {
      toast.error('Please enter a valid phone number');
      return;
    }

    setIsLoading(true);
    try {
      // Mock OTP generation
      const generatedOTP = Math.floor(1000 + Math.random() * 9000).toString();
      setMockOTP(generatedOTP);
      setOtpSent(true);
      
      // In production, this would call:
      // await customerApi.sendOTP({ phone: phoneNumber })
      
      // Mock success - show toast with OTP for testing
      toast.success(`OTP sent to ${phoneNumber}`);
      toast.info(`Mock OTP for testing: ${generatedOTP}`);
      
      setIsLoading(false);
    } catch {
      toast.error('Failed to send OTP. Please try again.');
      setIsLoading(false);
    }
  };

  const handleVerifyOTP = () => {
    if (!userOTP) {
      toast.error('Please enter the OTP');
      return;
    }

    if (userOTP === mockOTP) {
      setIsVerified(true);
      onVerified?.(true);
      toast.success('Phone number verified successfully');
    } else {
      toast.error('Invalid OTP. Please try again.');
    }
  };

  if (isVerified) {
    return (
      <div className="flex items-center gap-2 p-3 bg-green-50 border border-green-200 rounded-lg">
        <CheckCircle className="w-5 h-5 text-green-600" />
        <div>
          <p className="font-medium text-green-900 text-sm">{phoneNumber} Verified</p>
          <p className="text-xs text-green-700">Phone number verified successfully</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          <Phone className="w-4 h-4 inline mr-2" />
          Phone Number Verification
        </label>
        
        {!otpSent ? (
          <button
            onClick={handleSendOTP}
            disabled={disabled || isLoading || !phoneNumber}
            className="btn-outline w-full text-sm flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
            Send OTP to {phoneNumber}
          </button>
        ) : (
          <div className="space-y-2">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
              <p className="text-sm text-blue-900 font-medium">Enter OTP sent to {phoneNumber}</p>
              <input
                type="text"
                value={userOTP}
                onChange={(e) => setUserOTP(e.target.value.slice(0, 4))}
                placeholder="0000"
                maxLength={4}
                className="input-field mt-2 text-center text-2xl tracking-widest"
              />
            </div>

            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => {
                  setOtpSent(false);
                  setUserOTP('');
                  setMockOTP(null);
                }}
                className="btn-outline text-sm"
              >
                Change Number
              </button>
              <button
                onClick={handleVerifyOTP}
                disabled={!userOTP || userOTP.length !== 4}
                className="btn-primary text-sm"
              >
                Verify
              </button>
            </div>

            <p className="text-xs text-gray-500 text-center">
              Didn't receive OTP?{' '}
              <button
                onClick={handleSendOTP}
                className="text-bv-gold-600 hover:text-bv-gold-700 font-semibold"
              >
                Resend
              </button>
            </p>
          </div>
        )}
      </div>

      {/* Testing Info */}
      {mockOTP && !isVerified && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
          <p className="font-semibold">Testing Mode</p>
          <p>Mock OTP: <span className="font-mono font-bold text-amber-900">{mockOTP}</span></p>
          <p className="mt-1">In production, OTP would be sent via WhatsApp/SMS</p>
        </div>
      )}
    </div>
  );
}
