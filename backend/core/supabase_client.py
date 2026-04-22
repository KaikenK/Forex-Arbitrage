import os
import logging
from typing import Optional, Dict, Any
from supabase import create_client, Client

logger = logging.getLogger(__name__)

class SupabaseClient:
    """
    Singleton client to interact with Supabase for fetching secure credentials.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SupabaseClient, cls).__new__(cls)
            cls._instance._client = None
            cls._instance._is_initialized = False
        return cls._instance

    def initialize(self, supabase_url: str, supabase_key: str):
        if not self._is_initialized:
            try:
                self._client = create_client(supabase_url, supabase_key)
                self._is_initialized = True
                logger.info("Successfully initialized Supabase client.")
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                raise

    def get_mt5_credentials(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch MetaTrader 5 credentials for a specific user.
        Requires the Supabase client to be initialized with a Service Role key 
        or a key with read access to the user_credentials table.
        """
        if not self._is_initialized:
            logger.error("Supabase client not initialized.")
            return None
            
        try:
            response = self._client.table('user_credentials').select('*').eq('user_id', user_id).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching credentials for user {user_id}: {e}")
            return None

# Singleton instance
supabase_db = SupabaseClient()
