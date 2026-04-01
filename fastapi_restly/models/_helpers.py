from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Session

T = TypeVar("T", bound=DeclarativeBase)


def get_one_or_create(model_cls: type[T], session: Session, **kwargs: Any) -> T:
    """
    Return the unique row matching the given keyword arguments or create it.
    """
    select_query = select(model_cls).filter_by(**kwargs)
    results = session.scalars(select_query)
    try:
        return results.one()
    except NoResultFound:
        new_instance = model_cls(**kwargs)
        session.add(new_instance)
        session.flush()
        return new_instance


async def async_get_one_or_create(
    model_cls: type[T], session: AsyncSession, **kwargs: Any
) -> T:
    """
    Async variant of get_one_or_create.
    """
    select_query = select(model_cls).filter_by(**kwargs)
    results = await session.scalars(select_query)
    try:
        return results.one()
    except NoResultFound:
        new_instance = model_cls(**kwargs)
        session.add(new_instance)
        await session.flush()
        return new_instance
