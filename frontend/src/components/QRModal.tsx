import React from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { X, Copy, Check } from 'lucide-react';
import { useState } from 'react';

interface QRModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  configData: string;
  isVless: boolean;
}

const QRModal: React.FC<QRModalProps> = ({ isOpen, onClose, title, configData, isVless }) => {
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(configData);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy text: ', err);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-neutral-100">
          <h3 className="ui-card-title">{title}</h3>
          <button
            onClick={onClose}
            className="p-1 text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 flex flex-col items-center">
          {isVless && (
            <div className="bg-white p-4 rounded-xl border border-neutral-200 shadow-sm mb-6">
              <QRCodeSVG value={configData} size={200} level="M" includeMargin={false} />
            </div>
          )}

          <div className="w-full relative group">
            <div className="absolute right-2 top-2 z-10">
              <button
                onClick={handleCopy}
                className="p-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-md shadow-sm transition-colors"
                title="Копировать"
              >
                {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
              </button>
            </div>
            <textarea
              readOnly
              value={configData}
              className="w-full h-32 p-3 bg-neutral-50 border border-neutral-200 rounded-xl text-xs font-mono text-neutral-600 resize-none focus:outline-none"
            />
          </div>
        </div>

        <div className="p-4 border-t border-neutral-100 bg-neutral-50 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-neutral-200 hover:bg-neutral-300 text-neutral-800 text-sm font-medium rounded-lg transition-colors"
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
};

export default QRModal;
