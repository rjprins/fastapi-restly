import asyncio
import inspect
import sys
import threading

import pytest

from fastapi_restly._contextargs import contextual


@contextual
def greet(name, greeting="Hello"):
    return f"{greeting}, {name}!"


def test_direct_call_unaffected():
    assert greet("A") == "Hello, A!"


def test_direct_call_never_injects():
    with greet.context(name="World"):
        assert greet("A") == "Hello, A!"
        with pytest.raises(TypeError):
            greet()  # type: ignore  # intentionally missing args


def test_injection():
    with greet.context(name="World"):
        assert greet.context_call() == "Hello, World!"


def test_explicit_arguments_beat_context():
    with greet.context(name="World", greeting="Hi"):
        assert greet.context_call("A") == "Hi, A!"
        assert greet.context_call(greeting="Yo") == "Yo, World!"


def test_nesting_layers_and_exit_restores():
    with greet.context(name="outer"):
        with greet.context(greeting="Hi"):
            assert greet.context_call() == "Hi, outer!"
        assert greet.context_call() == "Hello, outer!"
    with pytest.raises(TypeError):
        greet.context_call()


def test_namespaced_per_function():
    @contextual
    def other(name="default"):
        return name

    with greet.context(name="World"):
        assert other.context_call() == "default"


def test_alias_is_independent_instance():
    greet_b = greet.alias("greet_b")

    with greet.context(name="original"):
        with greet_b.context(name="aliased"):
            assert greet.context_call() == "Hello, original!"
            assert greet_b.context_call() == "Hello, aliased!"
        # alias context gone, alias falls back to nothing; original unaffected
        assert greet.context_call() == "Hello, original!"
        with pytest.raises(TypeError):
            greet_b.context_call()


def test_alias_of_alias():
    a = greet.alias()
    b = a.alias()

    with a.context(name="a"), b.context(name="b"):
        assert a.context_call() == "Hello, a!"
        assert b.context_call() == "Hello, b!"


def test_named_alias_addressable_by_name():
    x = [greet.alias("greet2")]
    assert greet.alias("greet2") is x[0]

    with x[0].context(name="X"):
        assert greet.alias("greet2").context_call() == "Hello, X!"


def test_anonymous_alias_always_fresh():
    assert greet.alias() is not greet.alias()


def test_alias_registry_shared_across_family():
    a = greet.alias("family_a")
    assert a.alias("family_b") is greet.alias("family_b")
    assert greet.alias().alias("family_a") is a


def test_alias_name_in_error_message():
    named = greet.alias("special")
    with pytest.raises(TypeError, match="special"):
        with named.context(nope=1):
            pass


def test_unknown_name_rejected():
    with pytest.raises(TypeError, match="nope"):
        with greet.context(nope=1):
            pass


def test_positional_only_not_contextual():
    @contextual
    def f(x, /):
        return x

    with pytest.raises(TypeError, match="x"):
        with f.context(x=1):
            pass


def test_var_keyword_accepts_anything():
    @contextual
    def f(**kw):
        return kw

    with f.context(a=1, b=2):
        assert f.context_call(b=3) == {"a": 1, "b": 3}


def test_context_signature_introspection():
    sig = inspect.signature(greet.context)
    assert list(sig.parameters) == ["name", "greeting"]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values()
    )
    # all optional: any subset may be layered in from separate places
    assert all(
        p.default is not inspect.Parameter.empty for p in sig.parameters.values()
    )


def test_context_signature_with_var_keyword():
    @contextual
    def f(x, **kw):
        return x, kw

    # the synthetic signature reuses the function's own var-keyword name
    sig = inspect.signature(f.context)
    assert list(sig.parameters) == ["x", "kw"]
    assert sig.parameters["kw"].kind is inspect.Parameter.VAR_KEYWORD


def test_param_named_values_next_to_var_keyword():
    @contextual
    def f(values, **kwargs):
        return values, kwargs

    with f.context(values=1, extra=2):
        assert f.context_call() == (1, {"extra": 2})


def test_instance_method_rejected():
    with pytest.raises(TypeError, match="method"):

        class A:
            @contextual
            def m(self, x):
                return x


def test_classmethod_style_rejected():
    with pytest.raises(TypeError, match="method"):

        @contextual
        def m(cls, x):
            return x


def test_accepted_exposed():
    assert greet.accepted == frozenset({"name", "greeting"})


def test_accepted_none_for_var_keyword():
    @contextual
    def f(**kw):
        return kw

    assert f.accepted is None


def test_accepted_excludes_positional_only():
    @contextual
    def f(x, /, y):
        return x, y

    assert f.accepted == frozenset({"y"})


def test_family_shared_across_aliases():
    assert greet.alias("fam_named").family is greet.family
    assert greet.alias().family is greet.family
    assert greet.alias().alias("fam_deep").family is greet.family


def test_family_distinct_between_functions():
    @contextual
    def other(name):
        return name

    assert other.family is not greet.family


def test_async_function():
    @contextual
    async def whoami(name):
        await asyncio.sleep(0)
        return name

    if sys.version_info >= (3, 12):  # markcoroutinefunction exists from 3.12
        assert inspect.iscoroutinefunction(whoami)

    async def one(n):
        with whoami.context(name=n):
            await asyncio.sleep(0)
            return await whoami.context_call()

    async def main():
        return await asyncio.gather(*(one(f"task{i}") for i in range(5)))

    assert asyncio.run(main()) == [f"task{i}" for i in range(5)]


def test_threads_isolated():
    results = {}

    def worker(n):
        with greet.context(name=n):
            results[n] = greet.context_call()

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {f"t{i}": f"Hello, t{i}!" for i in range(4)}
