"""Read-only evaluator for exported Spark random-forest tree structures."""
import json
import math
from pathlib import Path


class Forest:
    def __init__(self, path):
        model = json.loads(Path(path).read_text())
        self.model = model
        self.labels = {name: i for i, name in enumerate(model['function_labels'])}
        self.trees = [{row['id']: row for row in tree} for tree in model['trees']]

    def predict(self, features):
        values = [float(self.labels.get(features['function_type'], len(self.labels)))
                  if col == 'function_index' else float(features[col])
                  for col in self.model['vector_columns']]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Non-finite model input')
        predictions = []
        for tree in self.trees:
            node = tree[0]
            while node['leftChild'] != -1:
                split = node['split']
                value = values[split['featureIndex']]
                left = value <= split['leftCategoriesOrThreshold'][0] if split['numCategories'] == -1 else value in split['leftCategoriesOrThreshold']
                node = tree[node['leftChild'] if left else node['rightChild']]
            predictions.append(node['prediction'])
        return sum(p * w for p, w in zip(predictions, self.model['tree_weights'])) / sum(self.model['tree_weights'])
