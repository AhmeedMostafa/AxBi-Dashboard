"""
Conversation persistence and sharing endpoints.

Stores chat conversations in Supabase for full-page viewing and public sharing.
"""

import json
import logging
import uuid
from datetime import datetime

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .supabase_client import verify_supabase_token, get_supabase_client

logger = logging.getLogger(__name__)


def _authenticate_request(request):
    auth_header = request.headers.get('Authorization', '')
    try:
        user_info = verify_supabase_token(auth_header)
        return user_info['user_id'], None
    except ValueError as e:
        return None, Response(
            {'error': 'Unauthorized', 'message': str(e)},
            status=status.HTTP_401_UNAUTHORIZED
        )


@api_view(['GET', 'POST'])
def conversations_list_view(request):
    """
    GET  /api/conversations/ — list user conversations
    POST /api/conversations/ — create a new conversation
    """
    user_id, err = _authenticate_request(request)
    if err:
        return err

    client = get_supabase_client()

    if request.method == 'GET':
        resp = (
            client.table('conversations')
            .select('*')
            .eq('user_id', user_id)
            .order('created_at', desc=True)
            .limit(50)
            .execute()
        )
        return Response({"conversations": resp.data or []})

    # POST — create
    title = request.data.get("title", "New Conversation")
    conv_id = str(uuid.uuid4())
    record = {
        "id": conv_id,
        "user_id": user_id,
        "title": title,
        "share_token": None,
        "created_at": datetime.utcnow().isoformat(),
    }
    client.table('conversations').insert(record).execute()
    return Response({"conversation": record}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def conversation_detail_view(request, conversation_id):
    """GET /api/conversations/:id/ — get conversation with messages"""
    user_id, err = _authenticate_request(request)
    if err:
        return err

    client = get_supabase_client()

    conv = (
        client.table('conversations')
        .select('*')
        .eq('id', conversation_id)
        .eq('user_id', user_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    msgs = (
        client.table('conversation_messages')
        .select('*')
        .eq('conversation_id', conversation_id)
        .order('created_at', desc=False)
        .execute()
    )

    return Response({
        "conversation": conv.data,
        "messages": msgs.data or [],
    })


@api_view(['POST'])
def conversation_message_view(request, conversation_id):
    """POST /api/conversations/:id/messages/ — add a message to conversation"""
    user_id, err = _authenticate_request(request)
    if err:
        return err

    client = get_supabase_client()

    conv = (
        client.table('conversations')
        .select('id')
        .eq('id', conversation_id)
        .eq('user_id', user_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    body = request.data
    msg_id = str(uuid.uuid4())
    record = {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": body.get("role", "user"),
        "content": body.get("content", ""),
        "chart_data": body.get("chart") if body.get("chart") else None,
        "visual_3d": body.get("visual3d") if body.get("visual3d") else None,
        "created_at": datetime.utcnow().isoformat(),
    }
    client.table('conversation_messages').insert(record).execute()
    return Response({"message": record}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
def conversation_share_view(request, conversation_id):
    """POST /api/conversations/:id/share/ — generate share token"""
    user_id, err = _authenticate_request(request)
    if err:
        return err

    client = get_supabase_client()

    conv = (
        client.table('conversations')
        .select('*')
        .eq('id', conversation_id)
        .eq('user_id', user_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    share_token = conv.data.get("share_token")
    if not share_token:
        share_token = str(uuid.uuid4())
        client.table('conversations').update({"share_token": share_token}).eq('id', conversation_id).execute()

    return Response({
        "share_token": share_token,
        "share_url": f"/share/{share_token}",
    })


@api_view(['DELETE'])
def conversation_delete_view(request, conversation_id):
    """DELETE /api/conversations/:id/ — permanently remove a conversation and its messages"""
    user_id, err = _authenticate_request(request)
    if err:
        return err

    client = get_supabase_client()

    conv = (
        client.table('conversations')
        .select('id')
        .eq('id', conversation_id)
        .eq('user_id', user_id)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    client.table('conversation_messages').delete().eq('conversation_id', conversation_id).execute()
    client.table('conversations').delete().eq('id', conversation_id).execute()
    return Response({"deleted": True, "conversation_id": conversation_id})


@api_view(['GET'])
def public_share_view(request, token):
    """GET /api/share/:token/ — public read-only conversation (no auth)"""
    client = get_supabase_client()

    conv = (
        client.table('conversations')
        .select('*')
        .eq('share_token', token)
        .maybe_single()
        .execute()
    )
    if not conv.data:
        return Response({"error": "Shared conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    msgs = (
        client.table('conversation_messages')
        .select('*')
        .eq('conversation_id', conv.data['id'])
        .order('created_at', desc=False)
        .execute()
    )

    return Response({
        "conversation": {
            "id": conv.data["id"],
            "title": conv.data.get("title", "Shared Conversation"),
            "created_at": conv.data.get("created_at"),
        },
        "messages": msgs.data or [],
    })
