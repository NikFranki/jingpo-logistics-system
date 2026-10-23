from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from auth.dependencies import business_user, web_write_user
from db import get_db
from agent_actions.schemas import ActionExecuteRequest, ActionPreviewRequest, ActionRejectRequest
from agent_actions.service import create_preview, execute_proposal, read_proposal, reject_proposal


router = APIRouter(prefix='/api/v1/agent-actions', tags=['agent-actions'])
Reader = Annotated[object, Depends(business_user)]
Writer = Annotated[object, Depends(web_write_user)]


@router.post('/preview', status_code=201)
def preview_action(body: ActionPreviewRequest, request: Request, response: Response,
                  principal: Writer, session: Annotated[Session, Depends(get_db)]):
    session.info['principal'] = principal
    with session.begin():
        result = create_preview(session, principal, body, _actor_session(session, principal))
    response.headers['Cache-Control'] = 'no-store'
    return result


def _actor_session(session: Session, principal) -> UUID:
    from agent_actions.service import _root_session
    root = _root_session(session, principal)
    return root.public_id


@router.post('/{proposal_id}/execute')
def execute_action(proposal_id: UUID, body: ActionExecuteRequest, request: Request,
                   response: Response, principal: Writer,
                   session: Annotated[Session, Depends(get_db)]):
    session.info['principal'] = principal
    result = execute_proposal(session, principal, proposal_id, body.mode, request.state.request_id)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.post('/{proposal_id}/reject')
def reject_action(proposal_id: UUID, body: ActionRejectRequest, response: Response,
                  principal: Writer, session: Annotated[Session, Depends(get_db)]):
    session.info['principal'] = principal
    result = reject_proposal(session, principal, proposal_id)
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.get('/{proposal_id}')
def action_status(proposal_id: UUID, response: Response, principal: Reader,
                  session: Annotated[Session, Depends(get_db)]):
    session.info['principal'] = principal
    with session.begin():
        result = read_proposal(session, principal, proposal_id)
    response.headers['Cache-Control'] = 'no-store'
    return result
