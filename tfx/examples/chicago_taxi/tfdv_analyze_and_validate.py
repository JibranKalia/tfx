# Copyright 2019 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Compute stats, infer schema, and validate stats for chicago taxi example."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse

import apache_beam as beam
import numpy as np
import tensorflow as tf
import tensorflow_data_validation as tfdv

from tensorflow_data_validation.coders import csv_decoder
from tensorflow_data_validation.utils import batch_util

from google.protobuf import text_format
from tensorflow.python.lib.io import file_io  # pylint: disable=g-direct-tensorflow-import
from tensorflow_metadata.proto.v0 import statistics_pb2

try:
  # Absolute import is preferred after 0.13 release, in which the path below
  # will be available in TFX package and will be a dependency of chicago taxi
  # example.
  from tfx.examples.chicago_taxi.trainer import taxi  # pylint: disable=g-import-not-at-top
except ImportError:
  from trainer import taxi  # pylint: disable=g-import-not-at-top


def infer_schema(stats_path, schema_path):
  """Infers a schema from stats in stats_path.

  Args:
    stats_path: Location of the stats used to infer the schema.
    schema_path: Location where the inferred schema is materialized.
  """
  print('Infering schema from statistics.')
  schema = tfdv.infer_schema(
      tfdv.load_statistics(stats_path), infer_feature_shape=False)
  print(text_format.MessageToString(schema))

  print('Writing schema to output path.')
  file_io.write_string_to_file(schema_path, text_format.MessageToString(schema))


def validate_stats(stats_path, schema_path, anomalies_path):
  """Validates the statistics against the schema and materializes anomalies.

  Args:
    stats_path: Location of the stats used to infer the schema.
    schema_path: Location of the schema to be used for validation.
    anomalies_path: Location where the detected anomalies are materialized.
  """
  print('Validating schema against the computed statistics.')
  schema = taxi.read_schema(schema_path)

  stats = tfdv.load_statistics(stats_path)
  anomalies = tfdv.validate_statistics(stats, schema)
  print('Detected following anomalies:')
  print(text_format.MessageToString(anomalies))

  print('Writing anomalies to anomalies path.')
  file_io.write_string_to_file(anomalies_path,
                               text_format.MessageToString(anomalies))


def compute_stats(input_handle,
                  stats_path,
                  max_rows=None,
                  for_eval=False,
                  pipeline_args=None):
  """Computes statistics on the input data.

  Args:
    input_handle: BigQuery table name to process specified as DATASET.TABLE or
      path to csv file with input data.
    stats_path: Directory in which stats are materialized.
    max_rows: Number of rows to query from BigQuery
    for_eval: Query for eval set rows from BigQuery
    pipeline_args: additional DataflowRunner or DirectRunner args passed to the
      beam pipeline.
  """

  with beam.Pipeline(argv=pipeline_args) as pipeline:
    if input_handle.lower().endswith('csv'):
      raw_data = (
          pipeline
          | 'ReadData' >> beam.io.textio.ReadFromText(
              file_pattern=input_handle, skip_header_lines=1)
          | 'DecodeData' >>
          csv_decoder.DecodeCSVToDict(column_names=taxi.CSV_COLUMN_NAMES))
    else:
      query = taxi.make_sql(
          table_name=input_handle, max_rows=max_rows, for_eval=for_eval)
      raw_data = (
          pipeline
          | 'ReadBigQuery' >> beam.io.Read(
              beam.io.BigQuerySource(query=query, use_standard_sql=True))
          | 'ConvertToTFDVInput' >> beam.Map(
              lambda x: {key: np.asarray([x[key]])  # pylint: disable=g-long-lambda
                         for key in x if x[key] is not None}))

    _ = (
        raw_data
        |
        'BatchExamplesToArrowTables' >> batch_util.BatchExamplesToArrowTables()
        | 'GenerateStatistics' >> tfdv.GenerateStatistics()
        | 'WriteStatsOutput' >> beam.io.WriteToTFRecord(
            stats_path,
            shard_name_template='',
            coder=beam.coders.ProtoCoder(
                statistics_pb2.DatasetFeatureStatisticsList)))


def main():
  tf.logging.set_verbosity(tf.logging.INFO)

  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--input',
      help=('Input BigQuery table to process specified as: '
            'DATASET.TABLE or path to csv file with input data.'))

  parser.add_argument(
      '--stats_path',
      help='Location for the computed stats to be materialized.')

  parser.add_argument(
      '--for_eval',
      help='Query for eval set rows from BigQuery',
      action='store_true')

  parser.add_argument(
      '--max_rows',
      help='Number of rows to query from BigQuery',
      default=None,
      type=int)

  parser.add_argument(
      '--schema_path',
      help='Location for the computed schema is located.',
      default=None,
      type=str)

  parser.add_argument(
      '--infer_schema',
      help='If specified, also infers a schema based on the computed stats.',
      action='store_true')

  parser.add_argument(
      '--validate_stats',
      help='If specified, also validates the stats against the schema.',
      action='store_true')

  parser.add_argument(
      '--anomalies_path',
      help='Location for detected anomalies are materialized.',
      default=None,
      type=str)

  known_args, pipeline_args = parser.parse_known_args()
  compute_stats(
      input_handle=known_args.input,
      stats_path=known_args.stats_path,
      max_rows=known_args.max_rows,
      for_eval=known_args.for_eval,
      pipeline_args=pipeline_args)
  print('Stats computation done.')

  if known_args.infer_schema:
    infer_schema(
        stats_path=known_args.stats_path, schema_path=known_args.schema_path)

  if known_args.validate_stats:
    validate_stats(
        stats_path=known_args.stats_path,
        schema_path=known_args.schema_path,
        anomalies_path=known_args.anomalies_path)


if __name__ == '__main__':
  main()
