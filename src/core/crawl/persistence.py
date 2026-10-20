"""Writing a crawl down, and picking it up again.

Batched saves, the queue checkpoint that makes a crashed crawl
resumable, and the background thread that keeps both current.

The batching is the point. A crawl of fifty thousand pages that wrote
each row as it arrived would spend most of its time waiting on SQLite,
so rows accumulate and go down in groups, with a checkpoint often enough
that a crash loses seconds rather than hours.

Lifted out of crawler.py unchanged to bring that file under the project's
1,000-line limit.
"""

import threading
import time


class PersistenceMixin:
    """Saving, checkpointing and resuming a crawl."""

    def _save_batch_to_db(self, force=False):
        """Save batched data to database"""
        if not self.db_save_enabled or not self.crawl_id:
            return

        from src.crawl_db import save_url_batch, save_links_batch, save_issues_batch, update_crawl_stats

        # Take the pending batches before touching the database. Crawl workers
        # keep appending during the write and the auto-save thread can run this
        # concurrently with the worker, so clearing the lists after the write
        # silently dropped everything appended in between. Each list keeps its
        # identity and only the rows actually taken are removed, so appends that
        # land mid-write survive and go out with the next batch.
        def take(batch):
            with self._batch_lock:
                taken = batch[:]
                del batch[:len(taken)]
            return taken

        urls = take(self.unsaved_urls)
        links = take(self.unsaved_links)
        issues = take(self.unsaved_issues)

        try:
            if urls:
                save_url_batch(self.crawl_id, urls)

            if links:
                save_links_batch(self.crawl_id, links)

            if issues:
                save_issues_batch(self.crawl_id, issues)

            # Update statistics
            memory_stats = self.memory_monitor.get_stats()
            update_crawl_stats(
                self.crawl_id,
                discovered=self.stats['discovered'],
                crawled=self.stats['crawled'],
                max_depth=self.stats['depth'],
                peak_memory_mb=memory_stats.get('peak_mb', 0),
                estimated_size_mb=memory_stats.get('estimated_crawl_mb', 0)
            )

            self.last_save_time = time.time()
            print(f"Saved batch to database for crawl {self.crawl_id}")

        except Exception as e:
            print(f"Error saving batch to database: {e}")
            import traceback
            traceback.print_exc()

    def _save_queue_checkpoint(self):
        """Save current queue state for crash recovery"""
        if not self.db_save_enabled or not self.crawl_id or not self.link_manager:
            return

        from src.crawl_db import save_checkpoint

        try:
            # Get discovered URLs from link manager
            discovered_urls = []
            if hasattr(self.link_manager, 'discovered_urls'):
                discovered_urls = list(self.link_manager.discovered_urls)[:1000]  # Limit to prevent huge checkpoints

            # Get visited URLs
            visited_urls = []
            if hasattr(self.link_manager, 'visited_urls'):
                visited_urls = list(self.link_manager.visited_urls)

            checkpoint = {
                'discovered_urls': discovered_urls,
                'visited_urls': visited_urls,
                'pending_count': self.link_manager.get_stats().get('pending', 0)
            }

            save_checkpoint(self.crawl_id, checkpoint)
            print(f"Saved queue checkpoint for crawl {self.crawl_id}")

        except Exception as e:
            print(f"Error saving checkpoint: {e}")

    def _start_auto_save_thread(self):
        """Background thread for periodic saves"""
        def auto_save_worker():
            while self.is_running:
                time.sleep(5)  # Check every 5 seconds
                if time.time() - self.last_save_time >= self.auto_save_interval:
                    self._save_batch_to_db()
                    self._save_queue_checkpoint()

        self.auto_save_thread = threading.Thread(target=auto_save_worker, daemon=True)
        self.auto_save_thread.start()
        print("Auto-save thread started")

