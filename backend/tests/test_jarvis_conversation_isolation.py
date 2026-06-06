"""
Test for JARVIS conversation history isolation (P2 bug fix).

Verifies that each user's conversation history is isolated and does not leak
across requests from different users.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from api.routers.jarvis import Jarvis


class TestJarvisConversationIsolation:
    """Verify conversation history is per-user, not shared globally."""

    @pytest.fixture
    def jarvis(self):
        """Create a fresh Jarvis instance for each test."""
        return Jarvis()

    @pytest.mark.asyncio
    async def test_conversation_history_isolated_per_user(self, jarvis):
        """Two users should have separate conversation histories."""
        # User A sends a query
        user_a_id = "user-a-superadmin"
        user_a_query = "What is our revenue today?"

        # Mock the analytics and NLP to avoid database calls
        jarvis.analytics = Mock()
        jarvis.analytics.get_business_overview = Mock(return_value={})
        jarvis.analytics.get_sales_insights = Mock(return_value={})
        jarvis.analytics.get_inventory_insights = Mock(return_value={})
        jarvis.analytics.get_customer_insights = Mock(return_value={})
        jarvis.analytics.get_staff_insights = Mock(return_value={})
        jarvis.analytics.get_predictions = Mock(return_value={})
        jarvis.analytics.get_recommendations = Mock(return_value=[])
        jarvis.analytics.get_extended_context = Mock(return_value={})

        jarvis.nlp = Mock()
        jarvis.nlp.detect_intent = Mock(return_value="sales")
        jarvis.nlp.extract_entities = Mock(return_value={})

        # Mock ClaudeClient.call_claude to return a response without using the API
        with patch(
            "api.routers.jarvis.ClaudeClient.call_claude", new_callable=AsyncMock
        ) as mock_claude, patch("agents.llm_provider.any_available", return_value=True):
            mock_claude.return_value = "Revenue today is Rs. 100,000"

            # User A makes a request
            result_a = await jarvis.process_query_async(
                user_a_query, context=None, model_id=None, user_id=user_a_id
            )

            assert result_a["ai_powered"] is True
            assert "Revenue today is Rs. 100,000" in result_a["response"]

            # Verify User A's history was stored
            assert user_a_id in jarvis.user_conversation_histories
            assert len(jarvis.user_conversation_histories[user_a_id]) == 2
            assert jarvis.user_conversation_histories[user_a_id][0]["content"] == user_a_query
            assert (
                "Revenue today is Rs. 100,000"
                in jarvis.user_conversation_histories[user_a_id][1]["content"]
            )

            # User B sends a different query
            user_b_id = "user-b-superadmin"
            user_b_query = "How many staff are present today?"
            mock_claude.return_value = "12 staff members are present"

            result_b = await jarvis.process_query_async(
                user_b_query, context=None, model_id=None, user_id=user_b_id
            )

            assert result_b["ai_powered"] is True
            assert "12 staff members are present" in result_b["response"]

            # Verify User B has a separate history (User A's query is NOT in B's history)
            assert user_b_id in jarvis.user_conversation_histories
            assert len(jarvis.user_conversation_histories[user_b_id]) == 2
            assert jarvis.user_conversation_histories[user_b_id][0]["content"] == user_b_query
            assert (
                "12 staff members are present"
                in jarvis.user_conversation_histories[user_b_id][1]["content"]
            )

            # Crucially: User B's history does NOT contain User A's query
            user_b_history_text = " ".join(
                msg["content"]
                for msg in jarvis.user_conversation_histories[user_b_id]
            )
            assert user_a_query not in user_b_history_text

            # And User A's history does NOT contain User B's query
            user_a_history_text = " ".join(
                msg["content"]
                for msg in jarvis.user_conversation_histories[user_a_id]
            )
            assert user_b_query not in user_a_history_text

            # Verify User A's history is untouched
            assert len(jarvis.user_conversation_histories[user_a_id]) == 2

    @pytest.mark.asyncio
    async def test_conversation_history_respects_20_message_limit_per_user(self, jarvis):
        """Each user's conversation history should be capped at 20 messages independently."""
        user_id = "user-with-long-history"

        jarvis.analytics = Mock()
        jarvis.analytics.get_business_overview = Mock(return_value={})
        jarvis.analytics.get_sales_insights = Mock(return_value={})
        jarvis.analytics.get_inventory_insights = Mock(return_value={})
        jarvis.analytics.get_customer_insights = Mock(return_value={})
        jarvis.analytics.get_staff_insights = Mock(return_value={})
        jarvis.analytics.get_predictions = Mock(return_value={})
        jarvis.analytics.get_recommendations = Mock(return_value=[])
        jarvis.analytics.get_extended_context = Mock(return_value={})

        jarvis.nlp = Mock()
        jarvis.nlp.detect_intent = Mock(return_value="general")
        jarvis.nlp.extract_entities = Mock(return_value={})

        with patch(
            "api.routers.jarvis.ClaudeClient.call_claude", new_callable=AsyncMock
        ) as mock_claude, patch("agents.llm_provider.any_available", return_value=True):
            # Send 15 queries to exceed the 20-message limit (30 messages total)
            for i in range(15):
                mock_claude.return_value = f"Response {i}"
                await jarvis.process_query_async(
                    f"Query {i}", context=None, model_id=None, user_id=user_id
                )

            # History should be trimmed to exactly 20 messages
            history = jarvis.user_conversation_histories[user_id]
            assert len(history) == 20

            # Verify the oldest messages were removed (keep only last 20)
            # The last message should be from query 14 (response 14)
            assert "Response 14" in history[-1]["content"]

            # The first message should be from query 5 (first query kept)
            # because we started with query 0 (2 msgs), ..., query 14 (2 msgs) = 30 msgs
            # keeping last 20 means starting from query 5 onwards
            assert "Query 5" in history[0]["content"]
