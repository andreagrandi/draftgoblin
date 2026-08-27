import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: root

    required property int pickIndex
    required property var recentPick
    signal hoverEntered()
    signal hoverExited()

    readonly property var card: root.recentPick && root.recentPick.card
        ? root.recentPick.card : null
    readonly property var imageState: root.recentPick && root.recentPick.image
        ? root.recentPick.image : null

    implicitHeight: width * 1.4
    objectName: "recentPickThumbnail" + root.pickIndex
    Accessible.role: Accessible.Graphic
    Accessible.name: root.card ? root.card.name : "Recent pick"
    Accessible.description: root.card
        ? root.card.name + ". Hover for a card preview."
        : "Hover for a card preview."

    Rectangle {
        anchors.fill: parent
        color: Theme.surface
        border.color: Theme.outline
        border.width: 1
        radius: Theme.radius
        clip: true

        Image {
            id: thumbnailImage
            objectName: "recentPickThumbnailImage"
            anchors.fill: parent
            anchors.margins: 2
            source: root.imageState
                && root.imageState.phase === "ready"
                && root.imageState.image_path
                ? root.imageState.image_path : ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            visible: status === Image.Ready
        }

        Label {
            id: fallbackLabel
            objectName: "recentPickThumbnailFallbackLabel"
            anchors.fill: parent
            anchors.margins: 5
            text: {
                if (thumbnailImage.status === Image.Error)
                    return "Image failed to display"
                if (root.imageState && root.imageState.phase === "loading")
                    return "Loading image"
                if (root.imageState && root.imageState.phase === "failed")
                    return "Image unavailable"
                if (root.imageState && root.imageState.phase === "ready"
                        && thumbnailImage.source.toString().length > 0)
                    return "Loading image"
                return "No image available"
            }
            color: Theme.textMuted
            font.pixelSize: 10
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            wrapMode: Text.WrapAnywhere
            visible: !thumbnailImage.visible
        }
    }

    HoverHandler {
        blocking: false
        onHoveredChanged: {
            if (hovered)
                root.hoverEntered()
            else
                root.hoverExited()
        }
    }
}
