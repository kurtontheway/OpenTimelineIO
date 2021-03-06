#
# Copyright 2017 Pixar Animation Studios
#
# Licensed under the Apache License, Version 2.0 (the "Apache License")
# with the following modification; you may not use this file except in
# compliance with the Apache License and the following modification to it:
# Section 6. Trademarks. is deleted and replaced with:
#
# 6. Trademarks. This License does not grant permission to use the trade
#    names, trademarks, service marks, or product names of the Licensor
#    and its affiliates, except as required to comply with Section 4(c) of
#    the License and to reproduce the content of the NOTICE file.
#
# You may obtain a copy of the Apache License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the Apache License with the above modification is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the Apache License for the specific
# language governing permissions and limitations under the Apache License.
#

"""Composition base class.  An object that contains `Items`."""

import collections

from . import (
    serializeable_object,
    type_registry,
    item,
    composable,
)

from .. import (
    opentime,
    exceptions
)


@type_registry.register_type
class Composition(item.Item, collections.MutableSequence):
    """Base class for an OTIO Item that contains other Items.

    Should be subclassed (for example by Sequence and Stack), not used
    directly.
    """

    _serializeable_label = "Composition.1"
    _composition_kind = "Composition"
    _modname = "core"
    _composable_base_class = composable.Composable

    def __init__(
        self,
        name=None,
        children=None,
        source_range=None,
        metadata=None
    ):
        item.Item.__init__(
            self,
            name=name,
            source_range=source_range,
            metadata=metadata
        )
        collections.MutableSequence.__init__(self)

        self._children = []
        if children:
            # cannot simply set ._children to children since __setitem__ runs
            # extra logic (assigning ._parent pointers).
            self.extend(children)

    _children = serializeable_object.serializeable_field(
        "children",
        list,
        "Items contained by this composition."
    )

    @property
    def composition_kind(self):
        """Returns a label specifying the kind of composition."""

        return self._composition_kind

    def __str__(self):
        return "{}({}, {}, {}, {})".format(
            self._composition_kind,
            str(self.name),
            str(self._children),
            str(self.source_range),
            str(self.metadata)
        )

    def __repr__(self):
        return (
            "otio.{}.{}("
            "name={}, "
            "children={}, "
            "source_range={}, "
            "metadata={}"
            ")".format(
                self._modname,
                self._composition_kind,
                repr(self.name),
                repr(self._children),
                repr(self.source_range),
                repr(self.metadata)
            )
        )

    transform = serializeable_object.deprecated_field()

    def each_child(
        self,
        search_range=None,
        descended_from_type=composable.Composable
    ):
        for i, child in enumerate(self._children):
            # filter out children who are not in the search range
            if (
                search_range
                and not self.range_of_child_at_index(i).overlaps(search_range)
            ):
                continue

            # filter out children who are not descneded from the specified type
            if (
                descended_from_type == composable.Composable
                or isinstance(child, descended_from_type)
            ):
                yield child

            # for children that are compositions, recurse into their children
            if isinstance(child, Composition):
                for valid_child in (
                    c for c in child.each_child(
                        search_range,
                        descended_from_type
                    )
                ):
                    yield valid_child

    def range_of_child_at_index(self, index):
        """Return the range of a child item in the time range of this
        composition.

        For example, with a sequence:
            [ClipA][ClipB][ClipC]

        The self.range_of_child_at_index(2) will return:
            TimeRange(ClipA.duration + ClipB.duration, ClipC.duration)

        To be implemented by subclass of Composition.
        """

        raise NotImplementedError

    def trimmed_range_of_child_at_index(self, index):
        """Return the trimmed range of the child item at index in the time
        range of this composition.

        For example, with a sequence:
                       [     ]
            [ClipA][ClipB][ClipC]

        The range of index 2 (ClipC) will be just like
        range_of_child_at_index() but trimmed based on this Composition's
        source_range.

        To be implemented by child.
        """

        raise NotImplementedError

    def __copy__(self):
        result = super(Composition, self).__copy__()

        # Children are *not* copied with a shallow copy since the meaning is
        # ambiguous - they have a parent pointer which would need to be flipped
        # or they would need to be copied, which implies a deepcopy().
        #
        # This follows from the python documentation on copy/deepcopy:
        # https://docs.python.org/2/library/copy.html
        #
        # """
        # - A shallow copy constructs a new compound object and then (to the
        #   extent possible) inserts references into it to the objects found in
        #   the original.
        # - A deep copy constructs a new compound object and then, recursively,
        #   inserts copies into it of the objects found in the original.
        # """
        result._children = []

        return result

    def __deepcopy__(self, md):
        result = super(Composition, self).__deepcopy__(md)

        # deepcopy should have already copied the children, so only parent
        # pointers need to be updated.
        [c._set_parent(result) for c in result._children]

        return result

    def _path_to_child(self, child):
        if not isinstance(child, item.Item):
            raise TypeError(
                "An object child of 'Item' is required, not type '{}'".format(
                    type(child)
                )
            )

        current = child
        parents = []

        while(current is not self):
            try:
                current = current._parent
            except AttributeError:
                raise exceptions.NotAChildError(
                    "Item '{}' is not a child of '{}'.".format(child, self)
                )

            parents.append(current)

        return parents

    def range_of_child(self, child, reference_space=None):
        """The range of the child in relation to another item
        (reference_space), not trimmed based on this based on this
        composition's source_range.

        Note that reference_space must be in the same timeline as self.

        For example,

        |     [-----]     | seq
        [-----------------] Clip A

        If ClipA has duration 17, and seq has source_range: 5, duration 15,
        seq.range_of_child(Clip A) will return (0, 17)
        ignoring the source range of seq.

        To get the range of the child with the source_range applied, use the
        trimmed_range_of_child() method.
        """

        if not reference_space:
            reference_space = self

        parents = self._path_to_child(child)

        result_range = child.source_range

        current = child
        result_range = None

        for parent in parents:
            index = parent.index(current)
            parent_range = parent.range_of_child_at_index(index)

            if not result_range:
                result_range = parent_range
                current = parent
                continue

            result_range.start_time = (
                result_range.start_time
                + parent_range.start_time
            )
            result_range.duration = result_range.duration
            current = parent

        if reference_space is not self:
            result_range = self.transformed_time_range(
                result_range,
                reference_space
            )

        return result_range

    def children_at_time(self, t):
        """ Which children overlap time t? """

        result = []
        for index, child in enumerate(self):
            if self.range_of_child_at_index(index).contains(t):
                result.append(child)

        return result

    def top_clip_at_time(self, t):
        """Return the first visible child that overlaps with time t."""

        for child in self.children_at_time(t):
            if isinstance(child, Composition):
                return child.top_clip_at_time(self.transformed_time(t, child))
            elif not child.visible():
                continue
            else:
                return child

        return None

    def trimmed_range_of_child(self, child, reference_space=None):
        """ Return range of the child in reference_space coordinates, after the
        self.source_range is applied.

        For example,

        |     [-----]     | seq
        [-----------------] Clip A

        If ClipA has duration 17, and seq has source_range: 5, duration 10,
        seq.trimmed_range_of_child(Clip A) will return (5, 10)
        Which is trimming the range according to the source_range of seq.

        To get the range of the child without the source_range applied, use the
        range_of_child() method.

        Another example:
        |  [-----]   | seq source range starts on frame 4 and goes to frame 8
        [ClipA][ClipB] (each 6 frames long)

        seq.range_of_child(CLipA):
            0, duration 6
        seq.trimmed_range_of_child(ClipA):
            4, duration 2
        """

        if not reference_space:
            reference_space = self

        if not reference_space == self:
            raise NotImplementedError

        parents = self._path_to_child(child)

        result_range = child.source_range

        current = child
        result_range = None

        for parent in parents:
            index = parent.index(current)
            parent_range = parent.trimmed_range_of_child_at_index(index)

            if not result_range:
                result_range = parent_range
                current = parent
                continue

            result_range.start_time = (
                result_range.start_time
                + parent_range.start_time
            )
            result_range.duration = result_range.duration
            current = parent

        if not self.source_range:
            return result_range

        new_start_time = max(
            self.source_range.start_time,
            result_range.start_time
        )

        # trimmed out
        if new_start_time >= result_range.end_time_exclusive():
            return None

        # compute duration
        new_duration = min(
            result_range.end_time_exclusive(),
            self.source_range.end_time_exclusive()
        ) - new_start_time

        if new_duration.value < 0:
            return None

        return opentime.TimeRange(new_start_time, new_duration)

    # @{ collections.MutableSequence implementation
    def __getitem__(self, item):
        return self._children[item]

    def __setitem__(self, key, value):
        value._set_parent(self)
        self._children[key] = value

    def insert(self, index, item):
        """Insert an item into the composition at location `index`."""

        if not isinstance(item, self._composable_base_class):
            raise TypeError(
                "Not allowed to insert an object of type {0} into a {1}, only"
                " objects descending from {2}. Tried to insert: {3}".format(
                    type(item),
                    type(self),
                    self._composable_base_class,
                    str(item)
                )
            )

        item._set_parent(self)
        self._children.insert(index, item)

    def __len__(self):
        return len(self._children)

    def __delitem__(self, item):
        thing = self._children[item]
        del self._children[item]
        thing._set_parent(None)
    # @}
