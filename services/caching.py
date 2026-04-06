import streamlit as st
import pandas as pd
import pickle
import hashlib
from pathlib import Path
from utils.xml_parser import XMLParser
from typing import Tuple, List, Dict, Optional

class CacheManager:
    """
    Optimized cache manager with disk persistence.
    - Uses MD5 hash for cache invalidation
    - Stores parsed TM data as pickle files
    - Hash-based O(1) exact match lookup
    """
    
    CACHE_DIR = Path("cache")
    
    @classmethod
    def _ensure_cache_dir(cls):
        """Create cache directory if it doesn't exist"""
        cls.CACHE_DIR.mkdir(exist_ok=True)
    
    @staticmethod
    def _compute_file_hash(content: bytes) -> str:
        """Compute MD5 hash of file content"""
        return hashlib.md5(content).hexdigest()
    
    @staticmethod
    def _get_cache_path(file_hash: str) -> Path:
        """Get pickle cache file path for given hash"""
        return CacheManager.CACHE_DIR / f"tm_cache_{file_hash}.pkl"
    
    @classmethod
    def clear_tm_cache(cls, file_hash: Optional[str] = None):
        """
        Clear TM cache files.
        If file_hash provided, clear only that cache.
        Otherwise, clear all TM caches.
        """
        cls._ensure_cache_dir()
        
        if file_hash:
            cache_path = cls._get_cache_path(file_hash)
            if cache_path.exists():
                cache_path.unlink()
                return True
            return False
        else:
            # Clear all TM caches
            count = 0
            for cache_file in cls.CACHE_DIR.glob("tm_cache_*.pkl"):
                cache_file.unlink()
                count += 1
            return count
    
    @classmethod
    def get_cache_info(cls) -> List[Dict]:
        """Get information about cached TM files"""
        cls._ensure_cache_dir()
        cache_files = []
        
        for cache_file in cls.CACHE_DIR.glob("tm_cache_*.pkl"):
            stat = cache_file.stat()
            cache_files.append({
                'file': cache_file.name,
                'size_mb': stat.st_size / (1024 * 1024),
                'modified': stat.st_mtime
            })
        
        return cache_files
    
    @classmethod
    def load_tm_data(cls, tmx_content: bytes, src_lang: str, tgt_lang: str) -> Tuple[List[Dict], Dict[str, str], str]:
        """
        Load TM data with disk caching.
        
        Returns:
            - entries: List of {'source': str, 'target': str}
            - exact_lookup: Dict for O(1) exact match {normalized_source: target}
            - file_hash: MD5 hash for cache management
        """
        cls._ensure_cache_dir()
        
        file_hash = cls._compute_file_hash(tmx_content)
        cache_path = cls._get_cache_path(file_hash)
        cache_key = f"{file_hash}_{src_lang}_{tgt_lang}"
        
        # Try to load from disk cache
        if cache_path.exists():
            try:
                with open(cache_path, 'rb') as f:
                    cached_data = pickle.load(f)
                    
                # Verify cache is for correct language pair
                if cached_data.get('cache_key') == cache_key:
                    return (
                        cached_data['entries'], 
                        cached_data['exact_lookup'],
                        file_hash
                    )
            except Exception as e:
                print(f"Cache load failed, reparsing: {e}")
        
        # Parse TMX file
        raw_entries = XMLParser.parse_tmx(tmx_content)
        
        entries = []
        exact_lookup = {}  # For O(1) exact match
        
        # Normalize language codes
        src_short = src_lang.split('-')[0].lower()
        tgt_short = tgt_lang.split('-')[0].lower()
        
        for entry in raw_entries:
            # Find matching keys
            s_key = next((k for k in entry.keys() if k.startswith(src_short)), None)
            t_key = next((k for k in entry.keys() if k.startswith(tgt_short)), None)
            
            if s_key and t_key and entry[s_key].strip():
                source_text = entry[s_key]
                target_text = entry[t_key]
                
                entries.append({
                    'source': source_text,
                    'target': target_text
                })
                
                # Build exact lookup (normalized key)
                normalized_key = source_text.strip().lower()
                exact_lookup[normalized_key] = target_text
        
        # Save to disk cache
        cache_data = {
            'cache_key': cache_key,
            'entries': entries,
            'exact_lookup': exact_lookup
        }
        
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(cache_data, f)
        except Exception as e:
            print(f"Cache save failed: {e}")
        
        return entries, exact_lookup, file_hash

    @staticmethod
    @st.cache_data
    def load_tb_data(csv_file) -> pd.DataFrame:
        """Loads and normalizes CSV termbase with encoding detection"""
        import io

        try:
            # Handle both bytes and file-like objects
            if isinstance(csv_file, bytes):
                content = csv_file
            else:
                content = csv_file.read() if hasattr(csv_file, 'read') else csv_file

            # Try different encodings
            encodings = ['utf-8-sig', 'utf-8', 'utf-16', 'latin-1', 'cp1252']
            df = None

            for encoding in encodings:
                try:
                    df = pd.read_csv(io.BytesIO(content), encoding=encoding)
                    break
                except Exception:
                    continue

            if df is None:
                return pd.DataFrame()

            # Convert to string, handling NaN values
            df = df.fillna('')
            df = df.astype(str)

            return df
        except Exception as e:
            print(f"TB load error: {e}")
            return pd.DataFrame()

    @classmethod
    def load_memoq_tb_data(cls, tb_results: List, tb_guid: str, search_terms: List) -> Tuple[List, str]:
        """Cache memoQ TB lookup results"""
        cls._ensure_cache_dir()

        cache_key = hashlib.md5(f"{tb_guid}_{'_'.join(search_terms)}".encode()).hexdigest()
        cache_path = cls.CACHE_DIR / f"memoq_tb_cache_{cache_key}.pkl"

        # Try load from cache
        if cache_path.exists():
            try:
                with open(cache_path, 'rb') as f:
                    return pickle.load(f), cache_key
            except:
                pass

        # Save to cache
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(tb_results, f)
        except:
            pass

        return tb_results, cache_key
