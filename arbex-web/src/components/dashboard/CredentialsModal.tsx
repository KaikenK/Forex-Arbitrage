"use client"

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Server, User, Key, Check, AlertCircle } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import { useAuthStore } from '@/store/authStore';

interface CredentialsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function CredentialsModal({ isOpen, onClose }: CredentialsModalProps) {
  const { user } = useAuthStore();
  const [mt5Login, setMt5Login] = useState('');
  const [mt5Password, setMt5Password] = useState('');
  const [mt5Server, setMt5Server] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    // Load existing credentials if any
    const loadCredentials = async () => {
      if (!user) return;
      try {
        const { data, error } = await supabase
          .from('user_credentials')
          .select('*')
          .eq('user_id', user.id)
          .single();
        
        if (data) {
          setMt5Login(data.mt5_login || '');
          setMt5Password(data.mt5_password || ''); // Usually masked or encrypted
          setMt5Server(data.mt5_server || '');
        }
      } catch (err) {
        console.error("Could not load credentials", err);
      }
    };
    
    if (isOpen) {
      loadCredentials();
      setSuccess(false);
      setError(null);
    }
  }, [isOpen, user]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    
    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      const { error } = await supabase
        .from('user_credentials')
        .upsert({ 
          user_id: user.id,
          mt5_login: mt5Login,
          mt5_password: mt5Password, // In a production app, use Supabase Vault here!
          mt5_server: mt5Server,
          updated_at: new Date().toISOString()
        }, { onConflict: 'user_id' });

      if (error) throw error;
      setSuccess(true);
      setTimeout(() => onClose(), 1500);
    } catch (err: any) {
      setError(err.message || 'Failed to securely save credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
          />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
            <motion.div 
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              className="bg-[#111] border border-white/10 rounded-2xl w-full max-w-md shadow-2xl pointer-events-auto overflow-hidden relative"
            >
              {/* Header */}
              <div className="flex items-center justify-between p-5 border-b border-white/5 bg-white/[0.02]">
                <h3 className="text-lg font-medium text-white">Execution Credentials</h3>
                <button 
                  onClick={onClose}
                  className="p-1 hover:bg-white/10 rounded-full transition-colors text-gray-400 hover:text-white"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Body */}
              <div className="p-6">
                <p className="text-sm text-gray-400 mb-6">
                  Securely configure your MetaTrader 5 account to allow the semantic engine to route trades automatically.
                </p>

                {error && (
                  <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg flex items-start gap-3 text-sm text-red-400">
                    <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                    <p>{error}</p>
                  </div>
                )}
                
                {success && (
                  <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg flex items-start gap-3 text-sm text-green-400">
                    <Check className="w-4 h-4 mt-0.5 shrink-0" />
                    <p>Credentials encrypted and stored securely.</p>
                  </div>
                )}

                <form onSubmit={handleSave} className="space-y-4">
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-400 uppercase tracking-wider ml-1">Account ID</label>
                    <div className="relative">
                      <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                      <input 
                        type="text" 
                        value={mt5Login}
                        onChange={(e) => setMt5Login(e.target.value)}
                        className="w-full bg-black/40 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-sm text-white focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/50 transition-all"
                        placeholder="e.g. 5019234"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-400 uppercase tracking-wider ml-1">Password</label>
                    <div className="relative">
                      <Key className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                      <input 
                        type="password" 
                        value={mt5Password}
                        onChange={(e) => setMt5Password(e.target.value)}
                        className="w-full bg-black/40 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-sm text-white focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/50 transition-all"
                        placeholder="••••••••••••"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-gray-400 uppercase tracking-wider ml-1">Server Name</label>
                    <div className="relative">
                      <Server className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                      <input 
                        type="text" 
                        value={mt5Server}
                        onChange={(e) => setMt5Server(e.target.value)}
                        className="w-full bg-black/40 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-sm text-white focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/50 transition-all"
                        placeholder="MetaQuotes-Demo"
                        required
                      />
                    </div>
                  </div>

                  <div className="pt-4 flex justify-end gap-3">
                    <button 
                      type="button"
                      onClick={onClose}
                      className="px-4 py-2 text-sm font-medium text-gray-300 hover:text-white transition-colors"
                    >
                      Cancel
                    </button>
                    <button 
                      type="submit"
                      disabled={loading || !user}
                      className="px-4 py-2 text-sm font-medium bg-white text-black rounded-lg hover:bg-gray-100 transition-colors flex items-center justify-center gap-2 min-w-[100px] disabled:opacity-50"
                    >
                      {loading ? (
                        <div className="w-4 h-4 border-2 border-black/20 border-t-black rounded-full animate-spin" />
                      ) : (
                        "Save API Keys"
                      )}
                    </button>
                  </div>
                </form>
              </div>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
