# Composite

![Composite: CommentNode interface, a leaf and a composite implementing it identically, the composite recursing into its own children](diagrams/composite.svg)

## The Problem It Solves

Individual items and groups-of-items need to be treated identically by calling code. Without this pattern, every piece of code that walks the structure has to special-case "is this one item, or a collection of them?" — and that check gets duplicated at every call site that touches the structure.

## How It Works

One interface declares the operations calling code needs (count something, render something). A leaf class implements it directly, answering for itself alone. A composite class implements the *same* interface but holds a list of children — each of which can itself be a leaf or another composite — and answers by combining its own contribution with the results of recursing into each child. A caller holding either kind, through the shared interface, never checks which one it has.

## Implementation

```
interface CommentNode:
    countReplies() -> int
    render(depth) -> string

class SingleComment implements CommentNode:
    author: string
    text: string

    countReplies():
        return 0
    render(depth):
        return indent(depth) + author + ": " + text

class CommentThread implements CommentNode:
    root: SingleComment
    children: List[CommentNode]        # each child can itself be a CommentThread -- that's the recursion

    countReplies():
        return len(children) + sum(child.countReplies() for child in children)
    render(depth):
        lines = [root.render(depth)]
        for child in children:
            lines.append(child.render(depth + 1))
        return "\n".join(lines)
```

Calling code that wants a reply count or a rendered thread calls `countReplies()` or `render()` on whatever `CommentNode` it has — it never checks "is this a leaf or a thread" first. A single top-level comment and a thread with 40 nested replies look identical to the caller.

## Real-World Use Case

A nested comment thread (any social platform's comment section — a reply can itself have replies, to arbitrary depth) or a file-system directory tree (a file and a folder both answer `size()`; a folder just sums its children's).

## When to Use It

- The domain is genuinely, recursively tree-shaped — items that can themselves contain more items, to arbitrary depth.
- Calling code should never need to branch on "is this a single item or a group" before acting on it.
- The same small set of operations (count, render, total, size) makes sense on both a leaf and a whole subtree.

## When NOT to Use It

If the structure never actually nests — it's always a flat list, never a group of groups — Composite adds recursive machinery for a shape that doesn't recur. A plain list and a loop is simpler and says exactly what's happening.

## Related Patterns

Composite is often walked using a separate Iterator (not covered as its own page here, but the idea recurs throughout this guide as pagination) when the traversal order itself needs to be swappable independently of the tree structure. It's also commonly combined with [Template Method](14-template-method.md): the composite's `render()` can define a fixed traversal skeleton while leaving what happens at each node to be filled in differently.
