import QtQuick 2.15
import QtQuick.Controls 2.15

FocusScope {
    id: root

    required property int pickIndex
    required property var recentPick
    signal activated(int grpId)

    readonly property var card: root.recentPick && root.recentPick.card
        ? root.recentPick.card : null
    readonly property var imageState: root.recentPick && root.recentPick.image
        ? root.recentPick.image : null
    readonly property bool keyboardFocused: root.activeFocus

    implicitHeight: width * 1.4
    activeFocusOnTab: true
    objectName: "recentPickThumbnail" + root.pickIndex
    Accessible.role: Accessible.Button
    Accessible.name: root.card ? root.card.name : "Recent pick"
    Accessible.description: root.card
        ? root.card.name + ". Press Enter or Space to preview this card."
        : "Press Enter or Space to preview this card."

    Keys.onReturnPressed: {
        if (root.card)
            root.activated(root.card.grp_id)
    }
    Keys.onSpacePressed: {
        if (root.card)
            root.activated(root.card.grp_id)
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.surface
        border.color: root.keyboardFocused ? Theme.focus : Theme.outline
        border.width: root.keyboardFocused ? 2 : 1
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

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            root.forceActiveFocus()
            if (root.card)
                root.activated(root.card.grp_id)
        }
    }
}
