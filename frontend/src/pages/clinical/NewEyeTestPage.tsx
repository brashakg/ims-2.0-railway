// ============================================================================
// IMS 2.0 - New Eye Test Page
// ============================================================================
// Simplified eye test page that redirects to patient queue

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Eye, ArrowLeft } from 'lucide-react';

export function NewEyeTestPage() {
  const navigate = useNavigate();

  useEffect(() => {
    // Redirect to clinical page (patient queue) after 2 seconds
    const timer = setTimeout(() => {
      navigate('/clinical');
    }, 2000);

    return () => clearTimeout(timer);
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center space-y-6 max-w-md">
        <div className="w-24 h-24 bg-purple-100 rounded-full flex items-center justify-center mx-auto">
          <Eye className="w-12 h-12 text-purple-600" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Start New Eye Test</h1>
          <p className="text-gray-500">
            To start a new eye test, please add the patient to the queue first.
          </p>
        </div>
        <div className="flex gap-3 justify-center">
          <button
            onClick={() => navigate('/clinical')}
            className="btn-primary flex items-center gap-2"
          >
            <ArrowLeft className="w-4 h-4" />
            Go to Patient Queue
          </button>
        </div>
        <p className="text-sm text-gray-400">Redirecting automatically...</p>
      </div>
    </div>
  );
}

export default NewEyeTestPage;
