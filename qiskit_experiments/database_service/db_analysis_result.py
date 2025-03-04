# This code is part of Qiskit.
#
# (C) Copyright IBM 2021.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Analysis result abstract interface."""

import logging
from typing import Optional, List, Union, Dict, Any
import uuid
import copy

from .database_service import DatabaseServiceV1
from .json import ExperimentEncoder, ExperimentDecoder
from .utils import save_data, qiskit_version
from .exceptions import DbExperimentDataError
from .device_component import DeviceComponent, to_component
from .db_fitval import FitVal


LOG = logging.getLogger(__name__)


class DbAnalysisResult:
    """Base common type for all versioned DbAnalysisResult abstract classes.

    Note this class should not be inherited from directly, it is intended
    to be used for type checking. When implementing a custom DbAnalysisResult
    you should use the versioned classes as the parent class and not this class
    directly.
    """

    version = 0


class DbAnalysisResultV1(DbAnalysisResult):
    """Class representing an analysis result for an experiment.

    Analysis results can also be stored in a database.
    """

    version = 1
    _data_version = 1

    _json_encoder = ExperimentEncoder
    _json_decoder = ExperimentDecoder

    _extra_data = {}

    def __init__(
        self,
        name: str,
        value: Any,
        device_components: List[Union[DeviceComponent, str]],
        experiment_id: str,
        result_id: Optional[str] = None,
        chisq: Optional[float] = None,
        quality: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        verified: bool = False,
        tags: Optional[List[str]] = None,
        service: Optional[DatabaseServiceV1] = None,
        source: Optional[Dict[str, str]] = None,
    ):
        """AnalysisResult constructor.

        Args:
            name: analysis result name.
            value: main analysis result value.
            device_components: Target device components this analysis is for.
            experiment_id: ID of the experiment.
            result_id: Result ID. If ``None``, one is generated.
            chisq: Reduced chi squared of the fit.
            quality: Quality of the analysis. Refer to the experiment service
                provider for valid values.
            extra: Dictionary of extra analysis result data
            verified: Whether the result quality has been verified.
            tags: Tags for this analysis result.
            service: Experiment service to be used to store result in database.
            source: Class and qiskit version information when loading from an
                experiment service.
        """
        # Data to be stored in DB.
        self._experiment_id = experiment_id
        self._id = result_id or str(uuid.uuid4())
        self._name = name
        self._value = copy.deepcopy(value)
        self._extra = copy.deepcopy(extra or {})
        self._device_components = []
        for comp in device_components:
            if isinstance(comp, str):
                comp = to_component(comp)
            self._device_components.append(comp)

        self._chisq = chisq
        self._quality = quality
        self._quality_verified = verified
        self._tags = tags or []

        # Other attributes.
        self._service = service
        self._source = source
        self._created_in_db = False
        self._auto_save = False
        if self._service:
            try:
                self.auto_save = self._service.preferences["auto_save"]
            except AttributeError:
                pass
        if self._source is None:
            self._source = {
                "class": f"{self.__class__.__module__}.{self.__class__.__name__}",
                "data_version": self._data_version,
                "qiskit_version": qiskit_version(),
            }

    @classmethod
    def load(cls, result_id: str, service: DatabaseServiceV1) -> "DbAnalysisResultV1":
        """Load a saved analysis result from a database service.

        Args:
            result_id: Analysis result ID.
            service: the database service.

        Returns:
            The loaded analysis result.
        """
        # Load data from the service
        service_data = service.analysis_result(result_id, json_decoder=cls._json_decoder)
        result = cls._from_service_data(service_data)
        result._created_in_db = True
        return result

    def save(self) -> None:
        """Save this analysis result in the database.

        Raises:
            DbExperimentDataError: If the analysis result contains invalid data.
        """
        if not self._service:
            LOG.warning(
                "Analysis result cannot be saved because no experiment service is available."
            )
            return
        # Get DB fit data
        value = self.value
        result_data = {
            "_value": value,
            "_extra": self.extra,
            "_source": self._source,
        }

        # Display compatible float values in in DB
        if isinstance(value, (int, float, bool)):
            result_data["value"] = float(value)
        elif isinstance(value, FitVal):
            if isinstance(value.value, (int, float)):
                result_data["value"] = value.value
            if isinstance(value.stderr, (int, float)):
                result_data["variance"] = value.stderr ** 2
            if isinstance(value.unit, str):
                result_data["unit"] = value.unit

        new_data = {
            "experiment_id": self._experiment_id,
            "result_type": self.name,
            "device_components": self.device_components,
        }
        update_data = {
            "result_id": self.result_id,
            "result_data": result_data,
            "tags": self.tags,
            "chisq": self._chisq,
            "quality": self.quality,
            "verified": self.verified,
        }

        self._created_in_db, _ = save_data(
            is_new=(not self._created_in_db),
            new_func=self._service.create_analysis_result,
            update_func=self._service.update_analysis_result,
            new_data=new_data,
            update_data=update_data,
            json_encoder=self._json_encoder,
        )

    @classmethod
    def _from_service_data(cls, service_data: Dict) -> "DbAnalysisResultV1":
        """Construct an analysis result from saved database service data.

        Args:
            service_data: Analysis result data.

        Returns:
            The loaded analysis result.
        """
        # Parse serialized data
        result_data = service_data.pop("result_data")
        value = result_data.pop("_value")
        extra = result_data.pop("_extra", {})
        source = result_data.pop("_source", None)

        # Initialize the result object
        obj = cls(
            name=service_data.pop("result_type"),
            value=value,
            device_components=service_data.pop("device_components"),
            experiment_id=service_data.pop("experiment_id"),
            result_id=service_data.pop("result_id"),
            quality=service_data.pop("quality"),
            extra=extra,
            verified=service_data.pop("verified"),
            tags=service_data.pop("tags"),
            service=service_data.pop("service"),
            source=source,
        )
        for key, val in service_data.items():
            setattr(obj, key, val)
        return obj

    @property
    def name(self) -> str:
        """Return analysis result name.

        Returns:
            Analysis result name.
        """
        return self._name

    @property
    def value(self) -> Any:
        """Return analysis result value.

        Returns:
            Analysis result value.
        """
        return self._value

    @value.setter
    def value(self, new_value: Any) -> None:
        """Set the analysis result value."""
        self._value = new_value
        if self.auto_save:
            self.save()

    @property
    def extra(self) -> Dict[str, Any]:
        """Return extra analysis result data.

        Returns:
            Additional analysis result data.
        """
        return self._extra

    @extra.setter
    def extra(self, new_value: Dict[str, Any]) -> None:
        """Set the analysis result value."""
        if not isinstance(new_value, dict):
            raise DbExperimentDataError(
                f"The `extra` field of {type(self).__name__} must be a dict."
            )
        self._extra = new_value
        if self.auto_save:
            self.save()

    @property
    def device_components(self) -> List[DeviceComponent]:
        """Return target device components for this analysis result.

        Returns:
            Target device components.
        """
        return self._device_components

    @property
    def result_id(self) -> str:
        """Return analysis result ID.

        Returns:
            ID for this analysis result.
        """
        return self._id

    @property
    def experiment_id(self) -> str:
        """Return the ID of the experiment associated with this analysis result.

        Returns:
            ID of experiment associated with this analysis result.
        """
        return self._experiment_id

    @property
    def chisq(self) -> Optional[float]:
        """Return the reduced χ² of this analysis."""
        return self._chisq

    @chisq.setter
    def chisq(self, new_chisq: float) -> None:
        """Set the reduced χ² of this analysis."""
        self._chisq = new_chisq
        if self.auto_save:
            self.save()

    @property
    def quality(self) -> str:
        """Return the quality of this analysis.

        Returns:
            Quality of this analysis.
        """
        return self._quality

    @quality.setter
    def quality(self, new_quality: str) -> None:
        """Set the quality of this analysis.

        Args:
            new_quality: New analysis quality.
        """
        self._quality = new_quality
        if self.auto_save:
            self.save()

    @property
    def verified(self) -> bool:
        """Return the verified flag.

        The ``verified`` flag is intended to indicate whether the quality
        value has been verified by a human.

        Returns:
            Whether the quality has been verified.
        """
        return self._quality_verified

    @verified.setter
    def verified(self, verified: bool) -> None:
        """Set the verified flag.

        Args:
            verified: Whether the quality is verified.
        """
        self._quality_verified = verified
        if self.auto_save:
            self.save()

    @property
    def tags(self):
        """Return tags associated with this result."""
        return self._tags

    @tags.setter
    def tags(self, new_tags: List[str]) -> None:
        """Set tags for this result."""
        if not isinstance(new_tags, list):
            raise DbExperimentDataError(
                f"The `tags` field of {type(self).__name__} must be a list."
            )
        self._tags = new_tags
        if self.auto_save:
            self.save()

    @property
    def service(self) -> Optional[DatabaseServiceV1]:
        """Return the database service.

        Returns:
            Service that can be used to store this analysis result in a database.
            ``None`` if not available.
        """
        return self._service

    @service.setter
    def service(self, service: DatabaseServiceV1) -> None:
        """Set the service to be used for storing result data in a database.

        Args:
            service: Service to be used.

        Raises:
            DbExperimentDataError: If an experiment service is already being used.
        """
        if self._service:
            raise DbExperimentDataError("An experiment service is already being used.")
        self._service = service

    @property
    def source(self) -> Dict:
        """Return the class name and version."""
        return self._source

    @property
    def auto_save(self) -> bool:
        """Return current auto-save option.

        Returns:
            Whether changes will be automatically saved.
        """
        return self._auto_save

    @auto_save.setter
    def auto_save(self, save_val: bool) -> None:
        """Set auto save preference.

        Args:
            save_val: Whether to do auto-save.
        """
        if save_val and not self._auto_save:
            self.save()
        self._auto_save = save_val

    def __str__(self):
        ret = f"{type(self).__name__}"
        ret += f"\n- name: {self.name}"
        ret += f"\n- value: {str(self.value)}"
        if self.chisq is not None:
            ret += f"\n- χ²: {str(self.chisq)}"
        if self.quality is not None:
            ret += f"\n- quality: {self.quality}"
        if self.extra:
            ret += f"\n- extra: <{len(self.extra)} items>"
        ret += f"\n- device_components: {[str(i) for i in self.device_components]}"
        ret += f"\n- verified: {self.verified}"
        return ret

    def __repr__(self):
        out = f"{type(self).__name__}("
        out += f"name={self.name}"
        out += f", value={repr(self.value)}"
        out += f", device_components={repr(self.device_components)}"
        out += f", experiment_id={self.experiment_id}"
        out += f", result_id={self.result_id}"
        out += f", chisq={self.chisq}"
        out += f", quality={self.quality}"
        out += f", verified={self.verified}"
        out += f", extra={repr(self.extra)}"
        out += f", tags={self.tags}"
        out += f", service={repr(self.experiment_id)}"
        for key, val in self._extra_data.items():
            out += f", {key}={repr(val)}"
        out += ")"
        return out
